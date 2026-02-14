"""
Proxy model — SOCKS5 proxies for Telegram accounts.

Each proxy can be assigned to multiple accounts.
Owner is the user who added the proxy.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class ProxyModel(Base):
    __tablename__ = "proxies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["UserModel"] = relationship(back_populates="proxies")  # noqa: F821
    accounts: Mapped[list["AccountModel"]] = relationship(  # noqa: F821
        back_populates="proxy"
    )

    @property
    def address(self) -> str:
        """Format as host:port string."""
        return f"{self.host}:{self.port}"

    @property
    def connection_string(self) -> str:
        """Format as socks5://user:pass@host:port."""
        if self.username and self.password:
            return f"socks5://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"socks5://{self.host}:{self.port}"

    def __repr__(self) -> str:
        return f"<Proxy {self.address}>"
