"""
Account repository — CRUD + queries for available/active accounts.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.account import AccountModel, AccountStatus
from src.db.repositories.base_repo import BaseRepository


class AccountRepository(BaseRepository[AccountModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AccountModel)

    async def get_by_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: AccountStatus | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[AccountModel]:
        """Get accounts belonging to an owner, optionally filtered by status."""
        stmt = (
            select(AccountModel)
            .where(AccountModel.owner_id == owner_id)
            .offset(offset)
            .limit(limit)
            .order_by(AccountModel.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(AccountModel.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_available_for_campaign(
        self, owner_id: uuid.UUID
    ) -> list[AccountModel]:
        """
        Get ACTIVE accounts that are NOT assigned to any campaign.

        Used when adding accounts to a new campaign.
        """
        from src.db.models.assignment import AssignmentModel, AssignmentStatus

        # Subquery: accounts that have active assignments
        assigned_subq = (
            select(AssignmentModel.account_id)
            .where(AssignmentModel.status == AssignmentStatus.ACTIVE)
            .distinct()
            .subquery()
        )

        stmt = (
            select(AccountModel)
            .where(
                AccountModel.owner_id == owner_id,
                AccountModel.status == AccountStatus.ACTIVE,
                AccountModel.id.notin_(select(assigned_subq)),
            )
            .order_by(AccountModel.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_phone(self, phone: str, owner_id: uuid.UUID) -> AccountModel | None:
        """Find account by phone number within an owner's accounts."""
        stmt = select(AccountModel).where(
            AccountModel.phone == phone,
            AccountModel.owner_id == owner_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_owner(
        self, owner_id: uuid.UUID, status: AccountStatus | None = None
    ) -> int:
        """Count accounts for an owner, optionally by status."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(AccountModel).where(
            AccountModel.owner_id == owner_id
        )
        if status is not None:
            stmt = stmt.where(AccountModel.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_with_proxy(self, account_id: uuid.UUID) -> AccountModel | None:
        """Get account with proxy eagerly loaded."""
        stmt = (
            select(AccountModel)
            .where(AccountModel.id == account_id)
            .options(selectinload(AccountModel.proxy))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
