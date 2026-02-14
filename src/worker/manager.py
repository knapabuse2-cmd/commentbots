"""
Worker manager — orchestrates all account workers across campaigns.

Responsibilities:
- Load active campaigns from DB
- Create AccountWorker for each active assignment
- Handle callbacks (ban → rotation, error → notify, comment → update stats)
- Periodic scan for new/changed campaigns
- Graceful shutdown of all workers

The manager runs as a background asyncio task alongside the admin bot.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import get_settings
from src.core.logging import get_logger
from src.db.models.account import AccountModel, AccountStatus
from src.db.models.assignment import AssignmentStatus
from src.db.models.campaign import CampaignStatus
from src.db.models.channel import ChannelStatus
from src.db.models.event_log import EventType
from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.assignment_repo import AssignmentRepository
from src.db.repositories.campaign_repo import CampaignRepository
from src.db.repositories.channel_repo import ChannelRepository
from src.db.repositories.event_log_repo import EventLogRepository
from src.services.distributor import DistributorService
from src.services.notification_service import NotificationService
from src.telegram.client import decrypt_session
from src.worker.account_worker import AccountWorker

if TYPE_CHECKING:
    from aiogram import Bot

log = get_logger(__name__)

# How often the manager scans for campaign changes
CAMPAIGN_SCAN_INTERVAL = 30  # seconds


class WorkerManager:
    """
    Manages all account workers across all campaigns.

    One manager per application instance.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bot: "Bot",
    ) -> None:
        self.session_factory = session_factory
        self.bot = bot

        settings = get_settings()
        self.connection_semaphore = asyncio.Semaphore(settings.worker_max_connections)

        # Track running workers: assignment_id → AccountWorker
        self._workers: dict[uuid.UUID, AccountWorker] = {}
        # Track running tasks: assignment_id → asyncio.Task
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}

        self._running = False
        self._scan_task: asyncio.Task | None = None

    # ============================================================
    # Lifecycle
    # ============================================================

    async def start(self) -> None:
        """Start the worker manager."""
        self._running = True
        log.info("worker_manager_starting")

        # Initial campaign load
        await self._scan_campaigns()

        # Start periodic scanner
        self._scan_task = asyncio.create_task(
            self._scan_loop(),
            name="worker-manager-scanner",
        )

        log.info(
            "worker_manager_started",
            active_workers=len(self._workers),
        )

    async def stop(self) -> None:
        """Stop all workers and the manager."""
        self._running = False
        log.info("worker_manager_stopping", workers=len(self._workers))

        # Cancel scanner
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        # Stop all workers
        stop_tasks = [worker.stop() for worker in self._workers.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        # Save all worker states
        await self._save_all_states()

        self._workers.clear()
        self._tasks.clear()

        log.info("worker_manager_stopped")

    # ============================================================
    # Campaign Scanner
    # ============================================================

    async def _scan_loop(self) -> None:
        """Periodically scan for campaign changes."""
        while self._running:
            try:
                await asyncio.sleep(CAMPAIGN_SCAN_INTERVAL)
                if self._running:
                    await self._scan_campaigns()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("campaign_scan_error", error=str(e))
                await asyncio.sleep(CAMPAIGN_SCAN_INTERVAL)

    async def _scan_campaigns(self) -> None:
        """
        Scan for active campaigns and sync workers:
        - Start workers for new assignments
        - Stop workers for removed/paused campaigns
        """
        async with self.session_factory() as session:
            try:
                campaign_repo = CampaignRepository(session)
                assignment_repo = AssignmentRepository(session)
                account_repo = AccountRepository(session)

                # Get all active campaigns
                active_campaigns = await campaign_repo.get_active_campaigns()

                # Collect all assignment IDs that should be running
                should_run: set[uuid.UUID] = set()

                for campaign in active_campaigns:
                    # Get active assignments for this campaign
                    assignments = await assignment_repo.get_active_for_campaign(campaign.id)

                    for assignment in assignments:
                        # Check account is available
                        if (
                            assignment.account
                            and assignment.account.is_available
                            and assignment.channel
                        ):
                            should_run.add(assignment.id)

                            # Start worker if not already running
                            if assignment.id not in self._workers:
                                await self._start_worker(
                                    session, campaign, assignment
                                )

                # Stop workers that should no longer run
                to_stop = set(self._workers.keys()) - should_run
                for assignment_id in to_stop:
                    await self._stop_worker(assignment_id)

                await session.commit()

                if should_run or to_stop:
                    log.debug(
                        "campaign_scan_complete",
                        active_workers=len(self._workers),
                        started=len(should_run - set(self._workers.keys())),
                        stopped=len(to_stop),
                    )

            except Exception as e:
                log.error("campaign_scan_error", error=str(e))
                await session.rollback()

    # ============================================================
    # Worker Management
    # ============================================================

    async def _start_worker(
        self,
        session: AsyncSession,
        campaign,
        assignment,
    ) -> None:
        """Create and start an AccountWorker for an assignment."""
        account = assignment.account
        channel = assignment.channel

        if not account.session_data:
            log.warning(
                "account_no_session_data",
                account_id=str(account.id)[:8],
            )
            return

        # Build proxy dict if account has one
        proxy = None
        if account.proxy_id:
            acc_with_proxy = await AccountRepository(session).get_with_proxy(account.id)
            if acc_with_proxy and acc_with_proxy.proxy:
                p = acc_with_proxy.proxy
                proxy = {
                    "host": p.host,
                    "port": p.port,
                    "username": p.username,
                    "password": p.password,
                }

        # Determine channel identifier
        channel_identifier = channel.username or channel.link
        if channel.invite_hash and not channel.username:
            channel_identifier = channel.link  # Use original link for invite channels

        worker = AccountWorker(
            account_id=account.id,
            account_phone=account.phone or account.display_name,
            session_data_encrypted=account.session_data,
            proxy=proxy,
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            message_text=campaign.message_text,
            message_entities=campaign.message_entities,
            message_photo_id=campaign.message_photo_id,
            channel_id=channel.id,
            channel_identifier=channel_identifier,
            channel_is_private=channel.is_private,
            channel_invite_hash=channel.invite_hash,
            assignment_id=assignment.id,
            assignment_state=assignment.state,
            owner_id=campaign.owner_id,
            on_banned=self._on_worker_banned,
            on_comment_posted=self._on_comment_posted,
            on_error=self._on_worker_error,
            on_no_posts=self._on_no_posts,
            connection_semaphore=self.connection_semaphore,
        )

        task = worker.start()
        self._workers[assignment.id] = worker
        self._tasks[assignment.id] = task

        log.info(
            "worker_started",
            assignment_id=str(assignment.id)[:8],
            account=account.display_name,
            channel=channel.display_name,
            campaign=campaign.name,
        )

    async def _stop_worker(self, assignment_id: uuid.UUID) -> None:
        """Stop a specific worker and save its state."""
        worker = self._workers.pop(assignment_id, None)
        task = self._tasks.pop(assignment_id, None)

        if worker:
            # Save state before stopping
            await self._save_worker_state(assignment_id, worker.get_state())
            await worker.stop()

            log.debug("worker_stopped", assignment_id=str(assignment_id)[:8])

    # ============================================================
    # Callbacks
    # ============================================================

    async def _on_worker_banned(
        self,
        account_id: uuid.UUID,
        channel_id: uuid.UUID,
        assignment_id: uuid.UUID,
        *,
        reason: str = "",
    ) -> None:
        """
        Handle account banned from channel.

        1. Mark assignment as BLOCKED (permanent)
        2. Mark channel as NO_ACCESS for this account
        3. Try to assign next free channel (rotation)
        4. Notify owner
        """
        log.warning(
            "worker_banned_callback",
            account_id=str(account_id)[:8],
            channel_id=str(channel_id)[:8],
            reason=reason,
        )

        # Remove worker from tracking
        self._workers.pop(assignment_id, None)
        self._tasks.pop(assignment_id, None)

        async with self.session_factory() as session:
            try:
                assign_repo = AssignmentRepository(session)
                channel_repo = ChannelRepository(session)
                event_repo = EventLogRepository(session)
                campaign_repo = CampaignRepository(session)

                # Mark assignment as blocked
                await assign_repo.mark_blocked(assignment_id)

                # Log event
                assignment = await assign_repo.get_by_id(assignment_id)
                owner_id = None
                campaign_id = None
                if assignment:
                    campaign = await campaign_repo.get_by_id(assignment.campaign_id)
                    if campaign:
                        owner_id = campaign.owner_id
                        campaign_id = campaign.id

                if owner_id:
                    await event_repo.log_event(
                        owner_id=owner_id,
                        event_type=EventType.CHANNEL_ACCESS_DENIED,
                        message=f"Account banned from channel: {reason}",
                        campaign_id=campaign_id,
                        account_id=account_id,
                        channel_id=channel_id,
                    )

                    # Try to assign next free channel
                    distributor = DistributorService(session)
                    new_assignment = await distributor.assign_next_channel(
                        campaign_id, account_id
                    )

                    if new_assignment:
                        await event_repo.log_event(
                            owner_id=owner_id,
                            event_type=EventType.CHANNEL_ROTATED,
                            message="Account rotated to new channel",
                            campaign_id=campaign_id,
                            account_id=account_id,
                            channel_id=new_assignment.channel_id,
                        )

                        # Notify
                        notif = NotificationService(self.bot, session)
                        await notif.notify(
                            owner_id,
                            EventType.CHANNEL_ROTATED,
                            f"Account rotated: banned → new channel assigned",
                        )
                    else:
                        # No free channels
                        await event_repo.log_event(
                            owner_id=owner_id,
                            event_type=EventType.NO_FREE_CHANNELS,
                            message="No free channels available for account",
                            campaign_id=campaign_id,
                            account_id=account_id,
                        )

                        notif = NotificationService(self.bot, session)
                        await notif.notify(
                            owner_id,
                            EventType.NO_FREE_CHANNELS,
                            f"No free channels! Account is waiting.",
                        )

                        # Set assignment to IDLE
                        if assignment:
                            # Account will wait until scan picks up a new free channel
                            pass

                await session.commit()

            except Exception as e:
                log.error("ban_callback_error", error=str(e))
                await session.rollback()

    async def _on_comment_posted(
        self,
        account_id: uuid.UUID,
        channel_id: uuid.UUID,
        assignment_id: uuid.UUID,
        *,
        comment_id: int = 0,
        post_id: int = 0,
    ) -> None:
        """Handle successful comment post."""
        async with self.session_factory() as session:
            try:
                assign_repo = AssignmentRepository(session)
                campaign_repo = CampaignRepository(session)
                event_repo = EventLogRepository(session)

                # Update assignment state
                await assign_repo.update_state(
                    assignment_id,
                    {
                        "current_comment_id": comment_id,
                        "current_post_id": post_id,
                        "last_comment_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                # Get campaign for stats
                assignment = await assign_repo.get_by_id(assignment_id)
                if assignment:
                    await campaign_repo.increment_stats(
                        assignment.campaign_id,
                        total=1,
                        successful=1,
                    )

                    # Log event
                    campaign = await campaign_repo.get_by_id(assignment.campaign_id)
                    if campaign:
                        await event_repo.log_event(
                            owner_id=campaign.owner_id,
                            event_type=EventType.COMMENT_POSTED,
                            message=f"Comment posted (id={comment_id}, post={post_id})",
                            campaign_id=campaign.id,
                            account_id=account_id,
                            channel_id=channel_id,
                            details={"comment_id": comment_id, "post_id": post_id},
                        )

                        # Notify
                        notif = NotificationService(self.bot, session)
                        await notif.notify(
                            campaign.owner_id,
                            EventType.COMMENT_POSTED,
                            f"Comment posted in channel",
                        )

                await session.commit()

            except Exception as e:
                log.error("comment_posted_callback_error", error=str(e))
                await session.rollback()

    async def _on_worker_error(
        self,
        account_id: uuid.UUID,
        channel_id: uuid.UUID,
        assignment_id: uuid.UUID,
        *,
        error: str = "",
    ) -> None:
        """Handle worker error."""
        log.error(
            "worker_error_callback",
            account_id=str(account_id)[:8],
            error=error,
        )

        # Remove worker from tracking
        self._workers.pop(assignment_id, None)
        self._tasks.pop(assignment_id, None)

        async with self.session_factory() as session:
            try:
                assign_repo = AssignmentRepository(session)
                campaign_repo = CampaignRepository(session)
                event_repo = EventLogRepository(session)

                # Increment fail count
                fail_count = await assign_repo.increment_fail_count(assignment_id)

                assignment = await assign_repo.get_by_id(assignment_id)
                if assignment:
                    campaign = await campaign_repo.get_by_id(assignment.campaign_id)
                    if campaign:
                        await event_repo.log_event(
                            owner_id=campaign.owner_id,
                            event_type=EventType.WORKER_ERROR,
                            message=f"Worker error (fails={fail_count}): {error}",
                            campaign_id=campaign.id,
                            account_id=account_id,
                            channel_id=channel_id,
                            details={"error": error, "fail_count": fail_count},
                        )

                        # Notify on errors
                        notif = NotificationService(self.bot, session)
                        await notif.notify_error(
                            campaign.owner_id,
                            f"Worker error: {error[:200]}",
                        )

                        # Update campaign stats
                        await campaign_repo.increment_stats(
                            campaign.id, total=1, failed=1,
                        )

                await session.commit()

            except Exception as e:
                log.error("error_callback_error", error=str(e))
                await session.rollback()

    async def _on_no_posts(
        self,
        account_id: uuid.UUID,
        channel_id: uuid.UUID,
        assignment_id: uuid.UUID,
    ) -> None:
        """Handle channel with no posts."""
        log.debug(
            "no_posts_callback",
            account_id=str(account_id)[:8],
            channel_id=str(channel_id)[:8],
        )
        # Just log, don't do anything — worker will keep checking

    # ============================================================
    # State Persistence
    # ============================================================

    async def _save_worker_state(
        self, assignment_id: uuid.UUID, state: dict
    ) -> None:
        """Save a single worker's state to DB."""
        async with self.session_factory() as session:
            try:
                assign_repo = AssignmentRepository(session)
                await assign_repo.update_state(assignment_id, state)
                await session.commit()
            except Exception as e:
                log.error("save_state_error", error=str(e))
                await session.rollback()

    async def _save_all_states(self) -> None:
        """Save all worker states to DB (called on shutdown)."""
        for assignment_id, worker in self._workers.items():
            await self._save_worker_state(assignment_id, worker.get_state())
        log.info("all_worker_states_saved", count=len(self._workers))

    # ============================================================
    # Stats
    # ============================================================

    def get_stats(self) -> dict:
        """Get current manager statistics."""
        return {
            "running_workers": len(self._workers),
            "semaphore_available": self.connection_semaphore._value,
            "is_running": self._running,
        }
