"""
Account model — Telegram user accounts for commenting.

Session data is stored encrypted (Fernet).
Each account belongs to one owner and optionally one campaign.
Status tracks the lifecycle: pending auth → active → banned/error.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class AccountStatus(str, enum.Enum):
    """Account lifecycle states."""
    PENDING = "pending"              # Created, not yet authorized
    AUTH_CODE = "auth_code"          # Waiting for SMS/call code
    AUTH_2FA = "auth_2fa"            # Waiting for 2FA password
    ACTIVE = "active"               # Authorized and ready to work
    PAUSED = "paused"               # Manually paused by user
    BANNED = "banned"               # Banned by Telegram globally
    ERROR = "error"                 # Session expired or other error


class AccountModel(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Encrypted Telethon StringSession
    session_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Path to tdata folder (if imported from Telegram Desktop)
    tdata_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status",
             values_callable=lambda e: [x.value for x in e]),
        nullable=False, default=AccountStatus.PENDING, index=True,
    )

    # Temporary auth data (cleared after successful auth)
    phone_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Proxy binding
    proxy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proxies.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Display info (cached from Telegram)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_id: Mapped[int | None] = mapped_column(nullable=True, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["UserModel"] = relationship(back_populates="accounts")  # noqa: F821
    proxy: Mapped["ProxyModel | None"] = relationship(back_populates="accounts")  # noqa: F821
    assignments: Mapped[list["AssignmentModel"]] = relationship(  # noqa: F821
        back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def is_available(self) -> bool:
        """Can this account be used for commenting right now?"""
        return self.status == AccountStatus.ACTIVE and self.session_data is not None

    @property
    def display_name(self) -> str:
        """Human-readable identifier for logs and UI."""
        if self.phone:
            return self.phone
        if self.first_name:
            return self.first_name
        return str(self.id)[:8]

    def __repr__(self) -> str:
        return f"<Account {self.display_name} status={self.status.value}>"
