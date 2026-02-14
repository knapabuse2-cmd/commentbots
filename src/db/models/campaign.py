"""
Campaign model — a commenting campaign with message template and channels.

Each campaign has:
- One message (text + optional photo) with original Telegram formatting
- A list of channels to comment in
- Assigned accounts that do the work
- Status lifecycle: draft → active → paused → completed
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class CampaignStatus(str, enum.Enum):
    """Campaign lifecycle states."""
    DRAFT = "draft"           # Created, not yet started
    ACTIVE = "active"         # Running — worker is commenting
    PAUSED = "paused"         # Temporarily stopped
    COMPLETED = "completed"   # All channels done or manually stopped


class CampaignModel(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"),
        nullable=False, default=CampaignStatus.DRAFT, index=True,
    )

    # Message template — stored as-is from Telegram
    message_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Telegram message entities (bold, italic, code, links etc.) as JSON
    # Stored in Telegram's native format so formatting is preserved 1:1
    message_entities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Photo file_id from Telegram (if message has a photo)
    message_photo_id: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Stats (updated by worker)
    total_comments: Mapped[int] = mapped_column(default=0, nullable=False)
    successful_comments: Mapped[int] = mapped_column(default=0, nullable=False)
    failed_comments: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["UserModel"] = relationship(back_populates="campaigns")  # noqa: F821
    channels: Mapped[list["ChannelModel"]] = relationship(  # noqa: F821
        back_populates="campaign", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["AssignmentModel"]] = relationship(  # noqa: F821
        back_populates="campaign", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Campaign '{self.name}' status={self.status.value}>"
