"""
Assignment model — links an account to a channel within a campaign.

This is the core working unit: one account works on one channel.
Stores all runtime state (current post, comment, profile copy status, etc.)
in a JSON field so the worker can resume after restart.

Key rule: each channel has AT MOST one active assignment.
An account can only be in one campaign, but works on multiple channels sequentially.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class AssignmentStatus(str, enum.Enum):
    """Assignment lifecycle states."""
    ACTIVE = "active"       # Account is working on this channel
    BLOCKED = "blocked"     # Account is banned/kicked from this channel (permanent)
    COMPLETED = "completed" # Done — moved to another channel
    IDLE = "idle"           # Waiting for a free channel


class AssignmentModel(Base):
    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, name="assignment_status"),
        nullable=False, default=AssignmentStatus.ACTIVE, index=True,
    )

    # Failure tracking
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Runtime state — all worker data in one JSON field
    # Structure:
    # {
    #   "current_post_id": int | null,       — last post we commented on
    #   "current_comment_id": int | null,     — our comment message ID
    #   "last_comment_at": str | null,        — ISO datetime of last comment
    #   "last_check_at": str | null,          — ISO datetime of last health check
    #   "profile_copied": bool,               — whether we copied channel profile
    #   "last_profile_copy_at": str | null,   — when profile was last copied
    # }
    state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default='{}',
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Ensure one ACTIVE assignment per channel (no two accounts on same channel).
    # Partial unique index: only enforced when status = 'active'.
    # BLOCKED/COMPLETED/IDLE assignments don't block new assignments.
    __table_args__ = (
        Index(
            "ix_one_active_per_channel",
            "channel_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    # Relationships
    campaign: Mapped["CampaignModel"] = relationship(back_populates="assignments")  # noqa: F821
    account: Mapped["AccountModel"] = relationship(back_populates="assignments")  # noqa: F821
    channel: Mapped["ChannelModel"] = relationship(back_populates="assignments")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<Assignment account={self.account_id!s:.8} "
            f"channel={self.channel_id!s:.8} status={self.status.value}>"
        )
