"""
Account worker — handles one account's lifecycle in one campaign.

Each account runs as an independent asyncio task.
The task loop:
1. Connect to Telegram via Telethon
2. Join discussion group (if not already)
3. Copy channel profile (if not done yet)
4. Find latest post → post comment
5. Wait repost_interval → delete old comment → repost
6. Every health_check_interval: verify our comment is alive
7. If banned/kicked → rotation (handled by manager callback)

Design principles:
- Each account works at MODERATE pace (60-120 sec between comments)
- Small actions (profile copy steps, join) separated by action_delay (3-5 sec)
- No rapid loops — sleep between major operations
- FloodWait is handled gracefully: sleep for the required time + buffer
- Reconnect on connection loss with exponential backoff
- Max retry limit on FloodWait to prevent infinite recursion
- All errors logged with full context
"""

import asyncio
import os
import random
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Coroutine

from src.core.config import get_settings
from src.core.exceptions import (
    AccountBannedError,
    AccountFloodWaitError,
    ChannelAccessDeniedError,
    ChannelCommentsDisabledError,
    ChannelNotFoundError,
    EncryptionError,
)
from src.core.logging import get_logger
from src.telegram.client import (
    CommentResult,
    check_comment_access,
    copy_channel_profile,
    create_client,
    decrypt_session,
    delete_comment,
    get_channel_info,
    get_latest_post,
    join_channel,
    post_comment,
)
from src.worker.entities import convert_entities

if TYPE_CHECKING:
    from telethon import TelegramClient

log = get_logger(__name__)

# Max consecutive FloodWait retries before giving up
MAX_FLOOD_RETRIES = 5

# Max reposts on the same channel before rotating to next one
MAX_REPOSTS_PER_CHANNEL = 2

# Max reconnection attempts before marking account as errored
MAX_RECONNECT_ATTEMPTS = 3

# Backoff multiplier for reconnection (seconds)
RECONNECT_BACKOFF_BASE = 30


class AccountWorker:
    """
    Independent worker for a single account on a single channel.

    Lifecycle:
    1. start() → connect, join, copy profile, enter comment loop
    2. comment loop: post → sleep → delete → repost → repeat
    3. health check: every N minutes verify comment exists
    4. stop() → disconnect, cleanup
    """

    def __init__(
        self,
        *,
        # Identity
        account_id: uuid.UUID,
        account_phone: str,
        session_data_encrypted: str,
        proxy: dict | None,
        # Campaign data
        campaign_id: uuid.UUID,
        campaign_name: str,
        message_text: str,
        message_entities: list[dict] | None,
        message_photo_id: str | None,
        # Channel data
        channel_id: uuid.UUID,
        channel_identifier: str,  # username or invite hash
        channel_is_private: bool,
        channel_invite_hash: str | None,
        # Assignment
        assignment_id: uuid.UUID,
        assignment_state: dict,
        # Owner
        owner_id: uuid.UUID,
        # Callbacks
        on_banned: Callable[..., Coroutine] | None = None,
        on_comment_posted: Callable[..., Coroutine] | None = None,
        on_error: Callable[..., Coroutine] | None = None,
        on_no_posts: Callable[..., Coroutine] | None = None,
        on_session_expired: Callable[..., Coroutine] | None = None,
        on_comment_reposted: Callable[..., Coroutine] | None = None,
        # Connection semaphore (shared across all workers)
        connection_semaphore: asyncio.Semaphore | None = None,
    ):
        self.account_id = account_id
        self.account_phone = account_phone
        self.session_data_encrypted = session_data_encrypted
        self.proxy = proxy

        self.campaign_id = campaign_id
        self.campaign_name = campaign_name
        self.message_text = message_text
        self.message_entities = message_entities
        self.message_photo_id = message_photo_id

        self.channel_id = channel_id
        self.channel_identifier = channel_identifier
        self.channel_is_private = channel_is_private
        self.channel_invite_hash = channel_invite_hash

        self.assignment_id = assignment_id
        self.state = dict(assignment_state) if assignment_state else {}

        self.owner_id = owner_id

        # Callbacks
        self.on_banned = on_banned
        self.on_comment_posted = on_comment_posted
        self.on_error = on_error
        self.on_no_posts = on_no_posts
        self.on_session_expired = on_session_expired
        self.on_comment_reposted = on_comment_reposted

        self.connection_semaphore = connection_semaphore

        # Runtime
        self.client: "TelegramClient | None" = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._current_comment_id: int | None = self.state.get("current_comment_id")
        self._current_post_id: int | None = self.state.get("current_post_id")
        self._profile_copied: bool = self.state.get("profile_copied", False)
        self._flood_retries: int = 0
        self._reconnect_attempts: int = 0

        # Settings
        settings = get_settings()
        self.min_comment_delay = settings.worker_min_comment_delay
        self.max_comment_delay = settings.worker_max_comment_delay
        self.action_delay = settings.worker_action_delay
        self.health_check_interval = settings.worker_health_check_interval
        self.repost_interval = settings.worker_repost_interval

        # Logging context — short for debug, full phone for important logs
        self._log_ctx = {
            "account": self.account_phone or str(self.account_id)[:8],
            "channel": self.channel_identifier,
            "campaign": self.campaign_name,
        }

    # ============================================================
    # Lifecycle
    # ============================================================

    def start(self) -> asyncio.Task:
        """Start the worker as an asyncio task."""
        self._running = True
        self._task = asyncio.create_task(
            self._run(),
            name=f"worker-{self.account_phone}-{self.channel_identifier}",
        )
        self._task.add_done_callback(self._on_task_done)
        log.debug("account_worker_starting", **self._log_ctx)
        return self._task

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._disconnect()
        log.debug("account_worker_stopped", **self._log_ctx)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Callback when task finishes (normally or with exception)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error(
                "account_worker_crashed",
                error=str(exc),
                error_type=type(exc).__name__,
                **self._log_ctx,
            )

    # ============================================================
    # Main Loop
    # ============================================================

    async def _run(self) -> None:
        """Main worker loop with reconnection and error recovery."""
        try:
            # Stagger start: random delay so workers don't all hit Telegram at once
            stagger = random.uniform(2, 15)
            await self._sleep(stagger)

            # Step 1: Connect
            await self._connect()
            self._reconnect_attempts = 0  # Reset on successful connect

            # Step 2: Join discussion group
            await self._sleep(random.uniform(2, 5))
            await self._ensure_joined()
            await self._sleep(self.action_delay)

            # Step 3: Copy profile (if not done yet)
            if not self._profile_copied:
                await self._copy_profile()
                await self._sleep(self.action_delay)

            # Step 4: Enter comment loop
            await self._comment_loop()

        except asyncio.CancelledError:
            log.debug("account_worker_cancelled", **self._log_ctx)
            raise

        except AccountBannedError as e:
            log.warning("account_globally_banned", error=str(e), **self._log_ctx)
            if self.on_session_expired:
                await self.on_session_expired(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="account_banned_globally",
                )
            elif self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="account_banned_globally",
                )

        except EncryptionError as e:
            log.error("session_decryption_failed", error=str(e), **self._log_ctx)
            if self.on_session_expired:
                await self.on_session_expired(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="session_decryption_failed",
                )

        except AccountFloodWaitError as e:
            self._flood_retries += 1
            if self._flood_retries > MAX_FLOOD_RETRIES:
                log.error(
                    "flood_wait_max_retries_exceeded",
                    retries=self._flood_retries,
                    **self._log_ctx,
                )
                if self.on_error:
                    await self.on_error(
                        self.account_id, self.channel_id, self.assignment_id,
                        error=f"Max flood retries ({MAX_FLOOD_RETRIES}) exceeded",
                    )
                return

            wait_time = e.seconds + random.randint(10, 30)
            log.warning(
                "account_flood_wait_retry",
                seconds=wait_time,
                retry=self._flood_retries,
                max_retries=MAX_FLOOD_RETRIES,
                **self._log_ctx,
            )
            await self._sleep(wait_time)
            if self._running:
                await self._disconnect()
                await self._run()

        except (ConnectionError, OSError, TimeoutError) as e:
            # Network errors — try to reconnect
            self._reconnect_attempts += 1
            if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
                log.error(
                    "max_reconnect_attempts_exceeded",
                    attempts=self._reconnect_attempts,
                    **self._log_ctx,
                )
                if self.on_error:
                    await self.on_error(
                        self.account_id, self.channel_id, self.assignment_id,
                        error=f"Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) exceeded: {e}",
                    )
                return

            backoff = RECONNECT_BACKOFF_BASE * self._reconnect_attempts
            log.warning(
                "connection_lost_reconnecting",
                error=str(e),
                attempt=self._reconnect_attempts,
                backoff=backoff,
                **self._log_ctx,
            )
            await self._disconnect()
            await self._sleep(backoff)
            if self._running:
                await self._run()

        except ChannelCommentsDisabledError as e:
            # Channel has no discussion group -> can't comment -> rotate to next channel
            log.warning("comments_disabled", error=str(e), **self._log_ctx)
            if self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason=f"comments_disabled: {self.channel_identifier}",
                )

        except ChannelNotFoundError as e:
            # Already handled in _ensure_joined (on_banned called there)
            log.debug("channel_not_found_in_run", error=str(e), **self._log_ctx)

        except ChannelAccessDeniedError as e:
            # Already handled inside _ensure_joined (which calls on_banned)
            log.debug("channel_error_in_run", error=str(e), **self._log_ctx)

        except Exception as e:
            log.error(
                "account_worker_fatal_error",
                error=str(e),
                error_type=type(e).__name__,
                **self._log_ctx,
            )
            if self.on_error:
                await self.on_error(
                    self.account_id, self.channel_id, self.assignment_id,
                    error=str(e),
                )
        finally:
            await self._disconnect()

    async def _comment_loop(self) -> None:
        """
        Main commenting loop:
        1. Find latest post
        2. Post comment
        3. Wait repost_interval
        4. Delete + repost
        5. Repeat

        Interleaves health checks every health_check_interval.
        """
        last_health_check = datetime.now(timezone.utc)
        discussion_rejoin_attempts = 0
        repost_count = 0

        while self._running:
            # -- HEALTH CHECK --
            now = datetime.now(timezone.utc)
            since_last_check = (now - last_health_check).total_seconds()

            if since_last_check >= self.health_check_interval:
                await self._health_check()
                last_health_check = datetime.now(timezone.utc)

            # -- GET LATEST POST --
            post = await self._get_latest_post()
            if post is None:
                log.debug("no_posts_in_channel", **self._log_ctx)
                if self.on_no_posts:
                    await self.on_no_posts(
                        self.account_id, self.channel_id, self.assignment_id,
                    )
                await self._sleep(self.repost_interval)
                continue

            post_id = post["id"]

            # -- DELETE OLD COMMENT (if exists on different post) --
            if self._current_comment_id and self._current_post_id != post_id:
                log.debug(
                    "new_post_detected_deleting_old",
                    old_post=self._current_post_id,
                    new_post=post_id,
                    **self._log_ctx,
                )
                await self._delete_current_comment()
                await self._sleep(self.action_delay)

            # -- POST COMMENT (if no current comment on this post) --
            if not self._current_comment_id or self._current_post_id != post_id:
                result = await self._post_comment(post_id)

                if result.is_banned:
                    log.warning("banned_in_channel", error=result.error, **self._log_ctx)
                    if self.on_banned:
                        await self.on_banned(
                            self.account_id, self.channel_id, self.assignment_id,
                            reason=result.error,
                        )
                    return  # Exit loop — manager handles rotation

                if result.is_channel_error:
                    log.warning("channel_error", error=result.error, **self._log_ctx)
                    if self.on_error:
                        await self.on_error(
                            self.account_id, self.channel_id, self.assignment_id,
                            error=result.error,
                        )
                    return

                if result.should_retry:
                    # Special case: need to re-join discussion group
                    if result.retry_after == 0 and "discussion group" in (result.error or ""):
                        discussion_rejoin_attempts += 1
                        if discussion_rejoin_attempts > 2:
                            # Gave up — treat as ban
                            log.warning("discussion_rejoin_gave_up", **self._log_ctx)
                            if self.on_banned:
                                await self.on_banned(
                                    self.account_id, self.channel_id, self.assignment_id,
                                    reason=result.error,
                                )
                            return

                        log.info("re_joining_discussion_group", attempt=discussion_rejoin_attempts, **self._log_ctx)
                        try:
                            await self._ensure_joined()
                            await self._sleep(self.action_delay)
                        except Exception as e:
                            log.warning(
                                "re_join_discussion_failed",
                                error=str(e),
                                **self._log_ctx,
                            )
                            if self.on_banned:
                                await self.on_banned(
                                    self.account_id, self.channel_id, self.assignment_id,
                                    reason=result.error,
                                )
                            return
                        continue  # Retry posting after re-join

                    retry_sec = result.retry_after + random.randint(5, 15)
                    log.info("flood_wait_sleeping", seconds=retry_sec, **self._log_ctx)
                    await self._sleep(retry_sec)
                    continue  # Retry the loop

                if result.success:
                    self._current_comment_id = result.message_id
                    self._current_post_id = post_id
                    self._flood_retries = 0  # Reset flood counter on success
                    discussion_rejoin_attempts = 0  # Reset rejoin counter on success

                    if self.on_comment_posted:
                        await self.on_comment_posted(
                            self.account_id, self.channel_id, self.assignment_id,
                            comment_id=result.message_id,
                            post_id=post_id,
                        )

                    log.info(
                        "comment_posted_successfully",
                        comment_id=result.message_id,
                        post_id=post_id,
                        **self._log_ctx,
                    )
                else:
                    log.warning("comment_post_failed", error=result.error, **self._log_ctx)

            # -- WAIT FOR REPOST --
            log.debug(
                "waiting_for_repost",
                seconds=self.repost_interval,
                **self._log_ctx,
            )

            # Sleep in chunks to check for cancellation and health checks
            elapsed = 0
            while elapsed < self.repost_interval and self._running:
                chunk = min(60, self.repost_interval - elapsed)
                await self._sleep(chunk)
                elapsed += chunk

                # Check health mid-wait
                now = datetime.now(timezone.utc)
                if (now - last_health_check).total_seconds() >= self.health_check_interval:
                    await self._health_check()
                    last_health_check = datetime.now(timezone.utc)

            if not self._running:
                return

            # -- DELETE + REPOST --
            if self._current_comment_id:
                log.debug("repost_cycle_starting", **self._log_ctx)

                # Get latest post again (might have new post)
                new_post = await self._get_latest_post()
                target_post_id = new_post["id"] if new_post else post_id

                # Delete old comment
                old_comment_id = self._current_comment_id
                await self._delete_current_comment()
                await self._sleep(self.action_delay)

                # Post new comment
                result = await self._post_comment(target_post_id)

                if result.is_banned:
                    if self.on_banned:
                        await self.on_banned(
                            self.account_id, self.channel_id, self.assignment_id,
                            reason=result.error,
                        )
                    return

                if result.is_channel_error:
                    log.warning("channel_error_on_repost", error=result.error, **self._log_ctx)
                    if self.on_error:
                        await self.on_error(
                            self.account_id, self.channel_id, self.assignment_id,
                            error=result.error,
                        )
                    return

                if result.should_retry:
                    retry_sec = result.retry_after + random.randint(5, 15)
                    await self._sleep(retry_sec)
                    continue

                if result.success:
                    self._current_comment_id = result.message_id
                    self._current_post_id = target_post_id
                    self._flood_retries = 0  # Reset flood counter on success
                    repost_count += 1

                    if self.on_comment_reposted:
                        await self.on_comment_reposted(
                            self.account_id, self.channel_id, self.assignment_id,
                            comment_id=result.message_id,
                            post_id=target_post_id,
                            old_comment_id=old_comment_id,
                        )

                    log.info(
                        "comment_reposted",
                        comment_id=result.message_id,
                        post_id=target_post_id,
                        repost_count=repost_count,
                        **self._log_ctx,
                    )

                    # After MAX_REPOSTS_PER_CHANNEL, rotate to next channel
                    if repost_count >= MAX_REPOSTS_PER_CHANNEL:
                        log.info(
                            "max_reposts_reached_rotating",
                            repost_count=repost_count,
                            **self._log_ctx,
                        )
                        if self.on_banned:
                            await self.on_banned(
                                self.account_id, self.channel_id, self.assignment_id,
                                reason="max_reposts_reached",
                            )
                        return
                else:
                    log.warning(
                        "repost_failed",
                        error=result.error,
                        **self._log_ctx,
                    )

    # ============================================================
    # Connection
    # ============================================================

    async def _connect(self) -> None:
        """Connect to Telegram."""
        if self.connection_semaphore:
            await self.connection_semaphore.acquire()

        try:
            session_str = decrypt_session(self.session_data_encrypted)
            self.client = create_client(
                session_string=session_str,
                proxy=self.proxy,
            )
            await self.client.connect()

            if not await self.client.is_user_authorized():
                raise AccountBannedError("Session is no longer authorized")

            me = await self.client.get_me()
            log.debug(
                "account_connected",
                telegram_id=me.id,
                name=me.first_name,
                **self._log_ctx,
            )
        except AccountBannedError:
            if self.connection_semaphore:
                self.connection_semaphore.release()
            raise
        except Exception as e:
            if self.connection_semaphore:
                self.connection_semaphore.release()
            # Frozen / deactivated accounts raise ForbiddenError at connect/get_me
            error_str = str(e).lower()
            if "frozen" in error_str or "deactivated" in error_str:
                raise AccountBannedError(f"Account frozen/deactivated: {e}")
            raise

    async def _disconnect(self) -> None:
        """Disconnect from Telegram."""
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

        if self.connection_semaphore:
            try:
                self.connection_semaphore.release()
            except ValueError:
                pass  # Already released

    # ============================================================
    # Join & Profile
    # ============================================================

    async def _ensure_joined(self) -> None:
        """
        Join the channel and its discussion group.

        For commenting, we need to be in the DISCUSSION GROUP.
        We also join the channel itself to read posts.
        """
        try:
            # Join channel first
            if self.channel_is_private and self.channel_invite_hash:
                await join_channel(self.client, invite_hash=self.channel_invite_hash)
            else:
                await join_channel(self.client, username=self.channel_identifier)

            await self._sleep(self.action_delay)

            # Get discussion group and join it too
            info = await get_channel_info(self.client, self.channel_identifier)

            if not info.has_comments or info.discussion_group_id is None:
                raise ChannelCommentsDisabledError(
                    f"Channel {self.channel_identifier} has no discussion group",
                    channel=self.channel_identifier,
                )

            # Join discussion group (needed for commenting)
            await join_channel(self.client, channel_id=info.discussion_group_id)

            log.debug(
                "joined_channel_and_discussion",
                discussion_group_id=info.discussion_group_id,
                **self._log_ctx,
            )

        except ChannelAccessDeniedError:
            log.debug("channel_access_denied_on_join", **self._log_ctx)
            if self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="access_denied_on_join",
                )
            raise
        except ChannelNotFoundError:
            log.debug("channel_not_found_on_join", **self._log_ctx)
            if self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="channel_not_found_on_join",
                )
            raise

    async def _copy_profile(self) -> None:
        """Copy channel name and avatar to account profile."""
        log.debug("copying_channel_profile", **self._log_ctx)

        result = copy_channel_profile(
            self.client,
            self.channel_identifier,
            copy_name=True,
            copy_avatar=True,
            action_delay=self.action_delay,
        )
        # copy_channel_profile is async
        result = await result

        if result["name_copied"] or result["avatar_copied"]:
            self._profile_copied = True
            log.debug(
                "profile_copied",
                name_copied=result["name_copied"],
                avatar_copied=result["avatar_copied"],
                **self._log_ctx,
            )
        else:
            log.warning(
                "profile_copy_incomplete",
                error=result.get("error"),
                **self._log_ctx,
            )

    # ============================================================
    # Commenting
    # ============================================================

    async def _get_latest_post(self) -> dict | None:
        """Get latest post from channel, handling errors."""
        try:
            return await get_latest_post(self.client, self.channel_identifier)
        except ChannelAccessDeniedError:
            if self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="access_denied_reading_posts",
                )
            raise
        except AccountFloodWaitError as e:
            log.warning("flood_wait_getting_posts", seconds=e.seconds, **self._log_ctx)
            await self._sleep(e.seconds + 10)
            return None

    async def _post_comment(self, post_id: int) -> CommentResult:
        """Post a comment under a post with the campaign message."""
        # Convert entities from stored format to Telethon objects
        telethon_entities = convert_entities(self.message_entities)

        # Moderate delay before posting
        delay = random.randint(self.min_comment_delay, self.max_comment_delay)
        log.debug("pre_comment_delay", seconds=delay, **self._log_ctx)
        await self._sleep(delay)

        if not self._running:
            return CommentResult(success=False, error="Worker stopped")

        # Determine photo path (downloaded at campaign creation time)
        photo_path = None
        if self.message_photo_id:
            candidate = f"data/photos/{self.campaign_id}.jpg"
            if os.path.exists(candidate):
                photo_path = candidate

        return await post_comment(
            self.client,
            self.channel_identifier,
            post_id,
            self.message_text,
            entities=telethon_entities,
            photo_path=photo_path,
        )

    async def _delete_current_comment(self) -> None:
        """Delete the current comment (if exists)."""
        if not self._current_comment_id:
            return

        try:
            deleted = await delete_comment(
                self.client,
                self.channel_identifier,
                self._current_comment_id,
            )
            if deleted:
                log.debug(
                    "comment_deleted",
                    comment_id=self._current_comment_id,
                    **self._log_ctx,
                )
            else:
                log.debug(
                    "comment_already_deleted",
                    comment_id=self._current_comment_id,
                    **self._log_ctx,
                )
        except AccountFloodWaitError as e:
            log.warning("flood_wait_deleting", seconds=e.seconds, **self._log_ctx)
            await self._sleep(e.seconds + 5)
        except Exception as e:
            log.warning(
                "comment_delete_failed",
                comment_id=self._current_comment_id,
                error=str(e),
                **self._log_ctx,
            )

        self._current_comment_id = None
        self._current_post_id = None

    # ============================================================
    # Health Check
    # ============================================================

    async def _health_check(self) -> None:
        """
        Check if our comment still exists and connection is alive.

        If the comment was deleted by channel admins:
        - Clear current_comment_id so it gets reposted on next loop iteration.

        Also checks Telethon client connection health.
        """
        # Check client connection
        if self.client and not self.client.is_connected():
            log.warning("client_disconnected_during_health_check", **self._log_ctx)
            raise ConnectionError("Telethon client disconnected")

        if not self._current_comment_id or not self._current_post_id:
            return

        log.debug("health_check_starting", **self._log_ctx)

        try:
            comment_alive = await check_comment_access(
                self.client,
                self.channel_identifier,
                self._current_comment_id,
            )

            if comment_alive:
                log.debug("health_check_ok", **self._log_ctx)
            else:
                log.info(
                    "comment_disappeared_reposting",
                    comment_id=self._current_comment_id,
                    **self._log_ctx,
                )
                self._current_comment_id = None
                # Will be reposted on next loop iteration

        except ChannelAccessDeniedError:
            log.warning("health_check_access_denied", **self._log_ctx)
            if self.on_banned:
                await self.on_banned(
                    self.account_id, self.channel_id, self.assignment_id,
                    reason="access_denied_health_check",
                )
        except AccountFloodWaitError as e:
            log.warning("health_check_flood_wait", seconds=e.seconds, **self._log_ctx)
            await self._sleep(e.seconds + 5)
        except (ConnectionError, OSError, TimeoutError) as e:
            log.warning("health_check_connection_error", error=str(e), **self._log_ctx)
            raise  # Propagate to _run() for reconnection

    # ============================================================
    # Helpers
    # ============================================================

    async def _sleep(self, seconds: float) -> None:
        """Sleep with cancellation support."""
        if not self._running:
            return
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self._running = False
            raise

    @property
    def is_alive(self) -> bool:
        """Check if the worker task is still running."""
        return self._task is not None and not self._task.done()

    def get_state(self) -> dict:
        """Get current state for persistence."""
        return {
            "current_post_id": self._current_post_id,
            "current_comment_id": self._current_comment_id,
            "profile_copied": self._profile_copied,
            "flood_retries": self._flood_retries,
            "reconnect_attempts": self._reconnect_attempts,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }
