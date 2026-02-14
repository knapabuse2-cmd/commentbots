"""
Event log model — records all significant events for debugging and stats.

Every action (comment posted, ban, rotation, error) is logged here.
This is the source of truth for "what happened and when".
Indexed for fast queries by owner, campaign, and time range.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class EventType(str, enum.Enum):
    """Types of events that can be logged."""
    # Comment lifecycle
    COMMENT_POSTED = "comment_posted"
    COMMENT_DELETED = "comment_deleted"
    COMMENT_REPOSTED = "comment_reposted"
    COMMENT_FAILED = "comment_failed"

    # Account events
    ACCOUNT_ADDED = "account_added"
    ACCOUNT_AUTHORIZED = "account_authorized"
    ACCOUNT_BANNED = "account_banned"
    ACCOUNT_ERROR = "account_error"

    # Channel events
    CHANNEL_JOINED = "channel_joined"
    CHANNEL_ACCESS_DENIED = "channel_access_denied"
    CHANNEL_ROTATED = "channel_rotated"       # Account moved to new channel
    CHANNEL_COMMENTS_DISABLED = "channel_comments_disabled"

    # Profile events
    PROFILE_COPIED = "profile_copied"
    PROFILE_COPY_FAILED = "profile_copy_failed"

    # Campaign events
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_PAUSED = "campaign_paused"
    CAMPAIGN_COMPLETED = "campaign_completed"

    # Worker events
    WORKER_STARTED = "worker_started"
    WORKER_STOPPED = "worker_stopped"
    WORKER_ERROR = "worker_error"

    # System
    NO_FREE_CHANNELS = "no_free_channels"     # Account has nowhere to go
    FLOOD_WAIT = "flood_wait"


class EventLogModel(Base):
    __tablename__ = "event_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type"),
        nullable=False, index=True,
    )

    # Human-readable message
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional context references (for filtering)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # Extra data (error details, flood wait seconds, etc.)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<Event {self.event_type.value} at {self.created_at}>"
