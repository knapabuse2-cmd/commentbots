"""
Channel model — Telegram channels where comments are posted.

Each channel belongs to one campaign.
Supports both public (@username, t.me/username) and private (invite link) channels.
Status tracks whether commenting is possible.
"""

import enum
import re
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

# Patterns for parsing Telegram channel links
_PATTERNS = [
    # t.me/username or t.me/+hash (invite)
    re.compile(r"(?:https?://)?t\.me/\+([a-zA-Z0-9_-]+)"),       # private invite
    re.compile(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})"),  # public
    # @username
    re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{3,})"),
    # Full URL formats
    re.compile(r"(?:https?://)?telegram\.me/\+([a-zA-Z0-9_-]+)"),
    re.compile(r"(?:https?://)?telegram\.me/([a-zA-Z][a-zA-Z0-9_]{3,})"),
]


class ChannelStatus(str, enum.Enum):
    """Channel availability states."""
    PENDING = "pending"              # Not yet checked
    ACTIVE = "active"                # Comments can be posted
    NO_ACCESS = "no_access"          # Cannot access the channel
    NO_COMMENTS = "no_comments"      # Comments are disabled
    ERROR = "error"                  # Other error


class ChannelModel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Original link as provided by user
    link: Mapped[str] = mapped_column(String(500), nullable=False)
    # Parsed username (for public channels) or None
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Parsed invite hash (for private channels) or None
    invite_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Telegram channel ID (resolved after first access)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, name="channel_status"),
        nullable=False, default=ChannelStatus.PENDING, index=True,
    )

    # Stats
    comments_posted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    campaign: Mapped["CampaignModel"] = relationship(back_populates="channels")  # noqa: F821
    assignments: Mapped[list["AssignmentModel"]] = relationship(  # noqa: F821
        back_populates="channel", cascade="all, delete-orphan"
    )

    @property
    def is_private(self) -> bool:
        """True if this is a private channel (invite link)."""
        return self.invite_hash is not None

    @property
    def display_name(self) -> str:
        """Human-readable identifier."""
        if self.username:
            return f"@{self.username}"
        if self.invite_hash:
            return f"private:{self.invite_hash[:8]}..."
        return self.link[:30]

    @staticmethod
    def parse_link(raw: str) -> tuple[str | None, str | None]:
        """
        Parse a channel link and extract username or invite hash.

        Returns:
            (username, None) for public channels
            (None, invite_hash) for private channels
            (None, None) if cannot parse

        Examples:
            "t.me/channel"      → ("channel", None)
            "@channel"          → ("channel", None)
            "t.me/+abc123"      → (None, "abc123")
        """
        raw = raw.strip()

        # Check invite link patterns first (t.me/+hash)
        for pattern in _PATTERNS:
            match = pattern.match(raw)
            if match:
                value = match.group(1)
                # If the original link had '+', it's a private invite
                if "+{}".format(value) in raw or "/+{}".format(value) in raw:
                    return None, value
                return value, None

        # Fallback: try as plain username
        clean = raw.lstrip("@").strip("/").strip()
        if clean and re.match(r"^[a-zA-Z][a-zA-Z0-9_]{3,}$", clean):
            return clean, None

        return None, None

    def __repr__(self) -> str:
        return f"<Channel {self.display_name} status={self.status.value}>"
