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

    async def count_by_owner(self, owner_id: uuid.UUID) -> int:
        """Count proxies for an owner."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(ProxyModel).where(
            ProxyModel.owner_id == owner_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

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

    async def get_unbound(self, owner_id: uuid.UUID) -> ProxyModel | None:
        """Get a proxy that has no accounts linked to it (1 proxy = 1 account)."""
        from src.db.models.account import AccountModel
        from sqlalchemy import func

        # Subquery: proxy_ids that already have accounts
        bound_ids = (
            select(AccountModel.proxy_id)
            .where(
                AccountModel.proxy_id.isnot(None),
                AccountModel.owner_id == owner_id,
            )
            .distinct()
            .scalar_subquery()
        )

        stmt = (
            select(ProxyModel)
            .where(
                ProxyModel.owner_id == owner_id,
                ProxyModel.id.notin_(bound_ids),
            )
            .order_by(ProxyModel.created_at.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
