"""
Proxy repository — CRUD for SOCKS5 proxies.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.proxy import ProxyModel
from src.db.repositories.base_repo import BaseRepository


class ProxyRepository(BaseRepository[ProxyModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProxyModel)

    async def get_by_owner(
        self,
        owner_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ProxyModel]:
        """Get all proxies belonging to an owner."""
        stmt = (
            select(ProxyModel)
            .where(ProxyModel.owner_id == owner_id)
            .offset(offset)
            .limit(limit)
            .order_by(ProxyModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_address(
        self, owner_id: uuid.UUID, host: str, port: int
    ) -> ProxyModel | None:
        """Find proxy by host:port (to avoid duplicates)."""
        stmt = select(ProxyModel).where(
            ProxyModel.owner_id == owner_id,
            ProxyModel.host == host,
            ProxyModel.port == port,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
