"""
User model — bot administrators/owners.

Each user has their own accounts, campaigns, proxies.
Identified by Telegram user ID.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Notification preferences: {"comments": true, "bans": true, "errors": true, "rotations": true}
    notification_prefs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default='{"comments": true, "bans": true, "errors": true, "rotations": true}'
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    accounts: Mapped[list["AccountModel"]] = relationship(  # noqa: F821
        back_populates="owner", cascade="all, delete-orphan"
    )
    campaigns: Mapped[list["CampaignModel"]] = relationship(  # noqa: F821
        back_populates="owner", cascade="all, delete-orphan"
    )
    proxies: Mapped[list["ProxyModel"]] = relationship(  # noqa: F821
        back_populates="owner", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id}>"
