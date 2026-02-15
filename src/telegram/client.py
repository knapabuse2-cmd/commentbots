"""
Telethon client wrapper — factory, session management, and high-level operations.

Handles:
- Client creation with proxy support
- Session encryption/decryption (Fernet)
- Authentication flow (phone → code → 2FA)
- Commenting on channel posts (with formatting preservation)
- Profile copying (name + avatar)
- Channel info retrieval
- Error classification

IMPORTANT NOTES (from Telethon docs research):
- To comment on a channel post, the account must be in the DISCUSSION GROUP
  (not the channel itself). Use GetFullChannelRequest to find linked_chat_id.
- comment_to parameter: entity = the CHANNEL, comment_to = post message ID.
  Telethon internally resolves the discussion group.
- formatting_entities: when passed, parse_mode is IGNORED. Entities are
  sent as-is, preserving exact formatting from the original message.
- Proxy format: dict with proxy_type, addr, port, username, password.
"""

import asyncio
import io
import os
import tempfile
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteRequestSentError,
    MsgIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionExpiredError,
    SessionPasswordNeededError,
    SessionRevokedError,
    SlowModeWaitError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
)
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.types import InputPhoto

from src.core.config import get_settings
from src.core.exceptions import (
    AccountAuthError,
    AccountBannedError,
    AccountFloodWaitError,
    ChannelAccessDeniedError,
    ChannelCommentsDisabledError,
    ChannelNotFoundError,
    CommentDeleteFailedError,
    CommentPostFailedError,
    EncryptionError,
)
from src.core.logging import get_logger

log = get_logger(__name__)


# ============================================================
# Session Encryption
# ============================================================


def _get_fernet() -> Fernet:
    """Get Fernet cipher from settings."""
    key = get_settings().session_encryption_key
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise EncryptionError(f"Invalid encryption key: {e}")


def encrypt_session(session_string: str) -> str:
    """Encrypt a Telethon StringSession for safe storage."""
    f = _get_fernet()
    return f.encrypt(session_string.encode()).decode()


def decrypt_session(encrypted: str) -> str:
    """Decrypt a stored session string."""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise EncryptionError("Failed to decrypt session — wrong key or corrupted data")


# ============================================================
# Comment Result
# ============================================================


@dataclass
class CommentResult:
    """Result of a comment operation — used by worker to decide next action."""

    success: bool
    message_id: int | None = None       # ID of posted comment (if success)
    error: str | None = None            # Human-readable error description
    is_banned: bool = False             # Account banned in this channel (permanent)
    is_channel_error: bool = False      # Channel-level issue (all accounts affected)
    should_retry: bool = False          # Temporary error, retry later (flood/slowmode)
    retry_after: int = 0                # Seconds to wait before retry


@dataclass
class ChannelInfo:
    """Resolved channel information."""

    channel_id: int
    title: str
    username: str | None
    discussion_group_id: int | None     # None = comments disabled
    has_comments: bool
    participants_count: int | None


# ============================================================
# Client Factory
# ============================================================


def create_client(
    session_string: str | None = None,
    proxy: dict | None = None,
) -> TelegramClient:
    """
    Create a TelegramClient instance.

    Args:
        session_string: Decrypted StringSession string, or None for new session.
        proxy: SOCKS5 proxy dict {proxy_type, addr, port, username?, password?}

    Returns:
        TelegramClient (not connected yet — call .connect() or use as context manager).
    """
    settings = get_settings()

    session = StringSession(session_string) if session_string else StringSession()

    proxy_dict = None
    if proxy:
        proxy_dict = {
            "proxy_type": "socks5",
            "addr": proxy["host"],
            "port": int(proxy["port"]),
        }
        if proxy.get("username"):
            proxy_dict["username"] = proxy["username"]
        if proxy.get("password"):
            proxy_dict["password"] = proxy["password"]
        proxy_dict["rdns"] = True  # Resolve DNS through proxy

    client = TelegramClient(
        session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=proxy_dict,
        # Connection settings for stability
        connection_retries=3,
        retry_delay=5,
        timeout=30,
        request_retries=3,
    )

    return client


# ============================================================
# Authentication
# ============================================================


async def send_auth_code(
    client: TelegramClient, phone: str
) -> str:
    """
    Send authentication code to phone number.

    Args:
        client: Connected TelegramClient.
        phone: Phone number with country code (e.g., +380...).

    Returns:
        phone_code_hash (needed for sign_in).

    Raises:
        AccountAuthError: If phone is invalid or banned.
        AccountFloodWaitError: If rate limited.
    """
    try:
        if not client.is_connected():
            await client.connect()

        result = await client.send_code_request(phone)
        log.info("auth_code_sent", phone=phone)
        return result.phone_code_hash

    except PhoneNumberInvalidError:
        raise AccountAuthError("Invalid phone number", phone=phone)
    except PhoneNumberBannedError:
        raise AccountBannedError("Phone number is banned by Telegram", phone=phone)
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds, phone=phone)


async def sign_in_with_code(
    client: TelegramClient, phone: str, code: str, phone_code_hash: str
) -> str:
    """
    Complete authentication with SMS code.

    Returns:
        Session string (encrypted) on success.

    Raises:
        AccountAuthError: If code is wrong/expired, or 2FA needed.
    """
    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        session_str = client.session.save()
        encrypted = encrypt_session(session_str)
        log.info("auth_sign_in_success", phone=phone)
        return encrypted

    except SessionPasswordNeededError:
        raise AccountAuthError("2FA password required", phone=phone, needs_2fa=True)
    except PhoneCodeInvalidError:
        raise AccountAuthError("Invalid verification code", phone=phone)
    except PhoneCodeExpiredError:
        raise AccountAuthError("Verification code expired — request a new one", phone=phone)
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds, phone=phone)


async def sign_in_with_2fa(
    client: TelegramClient, password: str
) -> str:
    """
    Complete authentication with 2FA password.

    Returns:
        Session string (encrypted) on success.
    """
    try:
        await client.sign_in(password=password)
        session_str = client.session.save()
        encrypted = encrypt_session(session_str)
        log.info("auth_2fa_success")
        return encrypted

    except PasswordHashInvalidError:
        raise AccountAuthError("Invalid 2FA password")
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds)


# ============================================================
# Channel Operations
# ============================================================


async def get_channel_info(
    client: TelegramClient, channel_identifier: str | int
) -> ChannelInfo:
    """
    Get full channel information including discussion group.

    Args:
        channel_identifier: Username (str) or channel ID (int).

    Raises:
        ChannelNotFoundError: Channel doesn't exist.
        ChannelAccessDeniedError: No access to channel.
    """
    try:
        entity = await client.get_entity(channel_identifier)
        result = await client(GetFullChannelRequest(entity))
        full = result.full_chat

        return ChannelInfo(
            channel_id=full.id,
            title=entity.title,
            username=getattr(entity, "username", None),
            discussion_group_id=full.linked_chat_id,
            has_comments=full.linked_chat_id is not None,
            participants_count=full.participants_count,
        )

    except (ChannelPrivateError, ChatAdminRequiredError):
        raise ChannelAccessDeniedError(
            "Cannot access channel", channel=str(channel_identifier)
        )
    except (ValueError, TypeError):
        raise ChannelNotFoundError(
            "Channel not found", channel=str(channel_identifier)
        )


async def join_channel(
    client: TelegramClient,
    *,
    username: str | None = None,
    invite_hash: str | None = None,
    channel_id: int | None = None,
) -> None:
    """
    Join a channel or group.

    For commenting, we need to join the DISCUSSION GROUP, not the channel.
    """
    try:
        if invite_hash:
            await client(ImportChatInviteRequest(invite_hash))
            log.debug("joined_channel_via_invite", invite_hash=invite_hash[:8])
        elif username:
            await client(JoinChannelRequest(username))
            log.debug("joined_channel", username=username)
        elif channel_id:
            await client(JoinChannelRequest(channel_id))
            log.debug("joined_channel", channel_id=channel_id)
        else:
            raise ValueError("Must provide username, invite_hash, or channel_id")

    except UserAlreadyParticipantError:
        # Already in the chat — this is fine, just skip
        log.debug("already_in_channel", invite_hash=invite_hash, username=username, channel_id=channel_id)
    except InviteHashExpiredError:
        # Invite link expired permanently — channel cannot be joined
        log.warning("invite_hash_expired", invite_hash=invite_hash)
        raise ChannelNotFoundError(
            "Invite link expired",
            channel=invite_hash or username or str(channel_id),
        )
    except InviteRequestSentError:
        # Channel requires admin approval — most have auto-accept
        # Wait 30 sec and try joining again (we should be accepted by then)
        log.info("invite_request_sent_waiting", invite_hash=invite_hash)
        await asyncio.sleep(30)

        # Try joining again — if accepted, we're already a participant
        try:
            if invite_hash:
                await client(ImportChatInviteRequest(invite_hash))
            elif username:
                await client(JoinChannelRequest(username))
            log.info("invite_request_accepted", invite_hash=invite_hash)
        except UserAlreadyParticipantError:
            # Auto-accept worked — we're in
            log.info("invite_request_accepted", invite_hash=invite_hash)
        except (InviteRequestSentError, ChannelPrivateError, InviteHashExpiredError):
            # Still not accepted or link died — give up
            log.warning("invite_request_not_accepted", invite_hash=invite_hash)
            raise ChannelAccessDeniedError(
                "Channel requires approval to join (not accepted after 30s)",
                channel=invite_hash or username or str(channel_id),
            )
        except FloodWaitError as e:
            raise AccountFloodWaitError(e.seconds)
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds)
    except ChannelPrivateError:
        raise ChannelAccessDeniedError("Channel is private, cannot join")


# ============================================================
# Commenting
# ============================================================


async def get_latest_post(
    client: TelegramClient, channel_identifier: str | int
) -> dict | None:
    """
    Get the most recent post from a channel.

    Returns:
        Dict with {id, text, date} or None if no posts.
    """
    try:
        messages = await client.get_messages(channel_identifier, limit=1)
        if not messages:
            return None

        msg = messages[0]
        return {
            "id": msg.id,
            "text": msg.text,
            "date": msg.date,
        }

    except (ChannelPrivateError, ChatAdminRequiredError):
        raise ChannelAccessDeniedError("Cannot access channel posts")
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds)


async def post_comment(
    client: TelegramClient,
    channel_identifier: str | int,
    post_id: int,
    text: str,
    *,
    entities: list | None = None,
    photo_path: str | None = None,
) -> CommentResult:
    """
    Post a comment under a channel post.

    The account must be in the discussion group for this to work.
    If entities are provided, they are sent as-is (formatting preserved 1:1).

    Args:
        channel_identifier: Channel username or entity.
        post_id: Message ID of the post to comment on.
        text: Comment text.
        entities: Telegram MessageEntity objects for formatting (optional).
        photo_path: Path to photo file to attach (optional).

    Returns:
        CommentResult with success status and message_id.
    """
    try:
        if photo_path and os.path.exists(photo_path):
            # Send photo with caption
            send_kwargs = {
                "entity": channel_identifier,
                "file": photo_path,
                "caption": text,
                "comment_to": post_id,
            }
            if entities:
                send_kwargs["formatting_entities"] = entities
            result = await client.send_file(**send_kwargs)
        else:
            # Send text-only comment
            send_kwargs = {
                "entity": channel_identifier,
                "message": text,
                "comment_to": post_id,
            }
            # Raw entities preserve exact formatting (bold, italic, code, links)
            if entities:
                send_kwargs["formatting_entities"] = entities
            result = await client.send_message(**send_kwargs)

        log.debug(
            "comment_posted",
            channel=str(channel_identifier),
            post_id=post_id,
            comment_id=result.id,
        )

        return CommentResult(success=True, message_id=result.id)

    except UserBannedInChannelError:
        return CommentResult(
            success=False,
            error="Account banned in this channel",
            is_banned=True,
        )

    except ChatWriteForbiddenError:
        return CommentResult(
            success=False,
            error="No write access — need to join discussion group",
            is_banned=True,  # Treat as permanent for this channel
        )

    except MsgIdInvalidError:
        return CommentResult(
            success=False,
            error="Post has no discussion thread (comments disabled)",
            is_channel_error=True,
        )

    except ChannelPrivateError:
        return CommentResult(
            success=False,
            error="Channel is private, no access",
            is_channel_error=True,
        )

    except FloodWaitError as e:
        return CommentResult(
            success=False,
            error=f"Flood wait: {e.seconds}s",
            should_retry=True,
            retry_after=e.seconds,
        )

    except SlowModeWaitError as e:
        return CommentResult(
            success=False,
            error=f"Slow mode: {e.seconds}s",
            should_retry=True,
            retry_after=e.seconds,
        )

    except Exception as e:
        # Catch ForbiddenError variants (CHAT_SEND_PLAIN_FORBIDDEN, etc.)
        error_name = type(e).__name__
        error_str = str(e)
        if "Forbidden" in error_name or "FORBIDDEN" in error_str:
            log.warning(
                "comment_post_forbidden",
                channel=str(channel_identifier),
                error=error_str,
            )
            return CommentResult(
                success=False,
                error=f"Forbidden: {error_str}",
                is_banned=True,
            )

        log.error(
            "comment_post_unexpected_error",
            channel=str(channel_identifier),
            error=error_str,
            error_type=error_name,
        )
        return CommentResult(
            success=False,
            error=f"Unexpected: {error_name}: {e}",
        )


async def delete_comment(
    client: TelegramClient,
    channel_identifier: str | int,
    comment_id: int,
) -> bool:
    """
    Delete our own comment from a channel's discussion group.

    Args:
        channel_identifier: Channel username or entity.
        comment_id: Message ID of the comment to delete.

    Returns:
        True if deleted successfully.
    """
    try:
        # To delete a comment, we need the discussion group entity
        # The comment lives in the discussion group, not the channel
        info = await get_channel_info(client, channel_identifier)
        if info.discussion_group_id is None:
            log.warning("delete_comment_no_discussion", channel=str(channel_identifier))
            return False

        await client.delete_messages(info.discussion_group_id, [comment_id])
        log.debug(
            "comment_deleted",
            channel=str(channel_identifier),
            comment_id=comment_id,
        )
        return True

    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds)
    except Exception as e:
        log.error(
            "comment_delete_error",
            comment_id=comment_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


# ============================================================
# Profile Copy
# ============================================================


async def copy_channel_profile(
    client: TelegramClient,
    channel_identifier: str | int,
    *,
    copy_name: bool = True,
    copy_avatar: bool = True,
    action_delay: int = 5,
) -> dict:
    """
    Copy a channel's name and avatar to the account's profile.

    IMPORTANT: For private channels (invite links), get_entity may resolve
    to the discussion group instead of the channel itself. We use
    GetFullChannelRequest to ensure we get the actual channel entity
    with the correct name and photo.

    Args:
        channel_identifier: Channel username or entity.
        copy_name: Whether to copy the channel title as first_name.
        copy_avatar: Whether to copy the channel photo as avatar.
        action_delay: Seconds to wait between actions (avoid flood).

    Returns:
        Dict with results: {"name_copied": bool, "avatar_copied": bool, "error": str|None}
    """
    result = {"name_copied": False, "avatar_copied": False, "error": None}

    try:
        entity = await client.get_entity(channel_identifier)

        # get_entity on invite links may return the discussion group (chat)
        # instead of the channel. Use GetFullChannelRequest to get the real
        # channel with correct title and photo.
        try:
            full_result = await client(GetFullChannelRequest(entity))
            # Use the entity from the full result — this is the actual channel
            channel_entity = full_result.chats[0]  # First chat is the channel itself
            log.debug(
                "profile_copy_resolved_channel",
                channel=str(channel_identifier),
                title=channel_entity.title,
            )
        except Exception:
            # Fallback to original entity if GetFullChannelRequest fails
            channel_entity = entity

    except Exception as e:
        result["error"] = f"Cannot get channel entity: {e}"
        return result

    # Copy name (from the CHANNEL, not discussion group)
    if copy_name:
        try:
            name = channel_entity.title[:64]  # Telegram limit
            await client(UpdateProfileRequest(
                first_name=name,
                last_name="",
            ))
            result["name_copied"] = True
            log.debug("profile_name_copied", channel=str(channel_identifier), name=name)
            await asyncio.sleep(action_delay)
        except FloodWaitError as e:
            result["error"] = f"Flood wait on name update: {e.seconds}s"
            return result
        except Exception as e:
            result["error"] = f"Name copy failed: {e}"
            log.warning("profile_name_copy_failed", error=str(e))

    # Copy avatar (from the CHANNEL, not discussion group)
    if copy_avatar:
        try:
            # Download channel photo to temp file
            photo_path = await client.download_profile_photo(
                channel_entity,
                file=os.path.join(tempfile.gettempdir(), f"avatar_{channel_entity.id}.jpg"),
                download_big=True,
            )

            if photo_path is None:
                log.debug("channel_has_no_avatar", channel=str(channel_identifier))
                return result

            await asyncio.sleep(action_delay)

            # Delete existing avatars
            my_photos = await client.get_profile_photos("me")
            if my_photos:
                await client(DeletePhotosRequest(
                    id=[
                        InputPhoto(
                            id=p.id,
                            access_hash=p.access_hash,
                            file_reference=p.file_reference,
                        )
                        for p in my_photos
                    ]
                ))
                await asyncio.sleep(action_delay)

            # Upload new avatar
            uploaded = await client.upload_file(photo_path)
            await client(UploadProfilePhotoRequest(file=uploaded))
            result["avatar_copied"] = True
            log.debug("profile_avatar_copied", channel=str(channel_identifier))

            # Clean up temp file
            try:
                os.remove(photo_path)
            except OSError:
                pass

        except FloodWaitError as e:
            result["error"] = f"Flood wait on avatar update: {e.seconds}s"
        except Exception as e:
            result["error"] = f"Avatar copy failed: {e}"
            log.warning("profile_avatar_copy_failed", error=str(e))

    return result


# ============================================================
# Session Validation
# ============================================================


async def validate_session(
    session_data_encrypted: str,
    proxy: dict | None = None,
) -> tuple[bool, str | None, dict | None]:
    """
    Check if an encrypted session is still valid.

    Returns:
        (is_valid, error_message, user_info)
        user_info = {"id": int, "first_name": str, "phone": str} if valid.
    """
    try:
        session_str = decrypt_session(session_data_encrypted)
    except EncryptionError as e:
        return False, str(e), None

    client = create_client(session_string=session_str, proxy=proxy)

    try:
        await client.connect()

        if not await client.is_user_authorized():
            return False, "Session is not authorized", None

        me = await client.get_me()
        user_info = {
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
            "username": me.username,
        }

        return True, None, user_info

    except (SessionExpiredError, SessionRevokedError, AuthKeyUnregisteredError) as e:
        return False, f"Session expired: {type(e).__name__}", None
    except UserDeactivatedError:
        return False, "Account deactivated by Telegram", None
    except FloodWaitError as e:
        return False, f"Flood wait: {e.seconds}s — try later", None
    except Exception as e:
        return False, f"Unexpected error: {type(e).__name__}: {e}", None
    finally:
        await client.disconnect()


# ============================================================
# Check Comment Access
# ============================================================


async def check_comment_access(
    client: TelegramClient,
    channel_identifier: str | int,
    comment_id: int,
) -> bool:
    """
    Check if our comment is still alive (not deleted by admins).

    Args:
        channel_identifier: Channel username or entity.
        comment_id: Our comment's message ID.

    Returns:
        True if comment still exists.
    """
    try:
        info = await get_channel_info(client, channel_identifier)
        if info.discussion_group_id is None:
            return False

        # Try to fetch the specific message
        messages = await client.get_messages(
            info.discussion_group_id,
            ids=[comment_id],
        )

        # get_messages returns None for deleted messages
        return messages and messages[0] is not None

    except (ChannelPrivateError, ChatAdminRequiredError, ChatWriteForbiddenError):
        return False
    except UserBannedInChannelError:
        return False
    except FloodWaitError as e:
        raise AccountFloodWaitError(e.seconds)
    except Exception as e:
        log.warning(
            "check_access_error",
            comment_id=comment_id,
            error=str(e),
        )
        return False
