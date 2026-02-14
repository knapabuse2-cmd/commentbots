"""
Notification service — sends alerts to users via Telegram bot.

Supports configurable notification types:
- comments: comment posted/deleted/failed
- bans: account banned/kicked from channel
- errors: critical errors, worker failures
- rotations: account moved to new channel

Users configure preferences in settings.
Notifications are sent via the bot instance (aiogram).
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.event_log import EventType
from src.db.repositories.user_repo import UserRepository

if TYPE_CHECKING:
    from aiogram import Bot

log = get_logger(__name__)

# Map event types to notification categories
_EVENT_CATEGORY: dict[EventType, str] = {
    EventType.COMMENT_POSTED: "comments",
    EventType.COMMENT_DELETED: "comments",
    EventType.COMMENT_REPOSTED: "comments",
    EventType.COMMENT_FAILED: "comments",
    EventType.ACCOUNT_BANNED: "bans",
    EventType.ACCOUNT_ERROR: "errors",
    EventType.CHANNEL_ACCESS_DENIED: "bans",
    EventType.CHANNEL_ROTATED: "rotations",
    EventType.CHANNEL_COMMENTS_DISABLED: "bans",
    EventType.PROFILE_COPIED: "comments",
    EventType.PROFILE_COPY_FAILED: "errors",
    EventType.CAMPAIGN_STARTED: "comments",
    EventType.CAMPAIGN_PAUSED: "comments",
    EventType.CAMPAIGN_COMPLETED: "comments",
    EventType.WORKER_STARTED: "errors",
    EventType.WORKER_STOPPED: "errors",
    EventType.WORKER_ERROR: "errors",
    EventType.NO_FREE_CHANNELS: "rotations",
    EventType.FLOOD_WAIT: "errors",
}

# Emoji for each event type
_EVENT_EMOJI: dict[EventType, str] = {
    EventType.COMMENT_POSTED: "\U0001f4ac",       # 💬
    EventType.COMMENT_DELETED: "\U0001f5d1",       # 🗑
    EventType.COMMENT_REPOSTED: "\U0001f504",      # 🔄
    EventType.COMMENT_FAILED: "\u274c",             # ❌
    EventType.ACCOUNT_BANNED: "\U0001f6ab",         # 🚫
    EventType.ACCOUNT_ERROR: "\U0001f534",          # 🔴
    EventType.CHANNEL_ACCESS_DENIED: "\U0001f6ab",  # 🚫
    EventType.CHANNEL_ROTATED: "\U0001f504",        # 🔄
    EventType.CHANNEL_COMMENTS_DISABLED: "\u26a0",  # ⚠
    EventType.PROFILE_COPIED: "\U0001f464",         # 👤
    EventType.PROFILE_COPY_FAILED: "\u26a0",        # ⚠
    EventType.CAMPAIGN_STARTED: "\u25b6",           # ▶
    EventType.CAMPAIGN_PAUSED: "\u23f8",            # ⏸
    EventType.CAMPAIGN_COMPLETED: "\u2705",         # ✅
    EventType.WORKER_STARTED: "\U0001f7e2",         # 🟢
    EventType.WORKER_STOPPED: "\U0001f534",         # 🔴
    EventType.WORKER_ERROR: "\U0001f534",           # 🔴
    EventType.NO_FREE_CHANNELS: "\u26a0",           # ⚠
    EventType.FLOOD_WAIT: "\u23f3",                 # ⏳
}


class NotificationService:
    """Sends notifications to users via Telegram bot."""

    def __init__(self, bot: "Bot", session: AsyncSession) -> None:
        self.bot = bot
        self.session = session
        self.user_repo = UserRepository(session)

    async def notify(
        self,
        owner_id: uuid.UUID,
        event_type: EventType,
        message: str,
    ) -> bool:
        """
        Send a notification to a user if they have this category enabled.

        Args:
            owner_id: User UUID.
            event_type: Type of event.
            message: Human-readable message text.

        Returns:
            True if sent, False if skipped (user disabled this category).
        """
        user = await self.user_repo.get_by_id(owner_id)
        if user is None:
            return False

        # Check if user wants this notification type
        category = _EVENT_CATEGORY.get(event_type, "errors")
        prefs = user.notification_prefs or {}
        if not prefs.get(category, True):
            return False  # User disabled this category

        emoji = _EVENT_EMOJI.get(event_type, "\u2139")  # ℹ default
        full_message = f"{emoji} {message}"

        try:
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=full_message,
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            log.warning(
                "notification_send_failed",
                user_id=str(owner_id)[:8],
                error=str(e),
            )
            return False

    async def notify_error(
        self, owner_id: uuid.UUID, message: str
    ) -> bool:
        """Shortcut for error notifications (always sent regardless of prefs)."""
        user = await self.user_repo.get_by_id(owner_id)
        if user is None:
            return False

        try:
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=f"\U0001f534 {message}",  # 🔴
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            log.warning("error_notification_failed", error=str(e))
            return False
