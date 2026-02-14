"""
Campaign repository — CRUD + queries for active campaigns.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.campaign import CampaignModel, CampaignStatus
from src.db.repositories.base_repo import BaseRepository


class CampaignRepository(BaseRepository[CampaignModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CampaignModel)

    async def get_by_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: CampaignStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[CampaignModel]:
        """Get campaigns for an owner, optionally filtered by status."""
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.owner_id == owner_id)
            .offset(offset)
            .limit(limit)
            .order_by(CampaignModel.created_at.desc())
        )
        if status is not None:
            stmt = stmt.where(CampaignModel.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_campaigns(self) -> list[CampaignModel]:
        """
        Get ALL active campaigns across all users.

        Used by worker to find campaigns that need processing.
        Eagerly loads channels and assignments for efficiency.
        """
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.status == CampaignStatus.ACTIVE)
            .options(
                selectinload(CampaignModel.channels),
                selectinload(CampaignModel.assignments),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_details(self, campaign_id: uuid.UUID) -> CampaignModel | None:
        """Get campaign with channels and assignments eagerly loaded."""
        stmt = (
            select(CampaignModel)
            .where(CampaignModel.id == campaign_id)
            .options(
                selectinload(CampaignModel.channels),
                selectinload(CampaignModel.assignments),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def increment_stats(
        self,
        campaign_id: uuid.UUID,
        *,
        total: int = 0,
        successful: int = 0,
        failed: int = 0,
    ) -> None:
        """Increment campaign comment statistics atomically."""
        campaign = await self.get_by_id(campaign_id)
        if campaign is None:
            return
        campaign.total_comments += total
        campaign.successful_comments += successful
        campaign.failed_comments += failed
        await self.session.flush()

    async def count_by_owner(
        self, owner_id: uuid.UUID, status: CampaignStatus | None = None
    ) -> int:
        """Count campaigns for an owner, optionally by status."""
        from sqlalchemy import func

        stmt = select(func.count()).select_from(CampaignModel).where(
            CampaignModel.owner_id == owner_id
        )
        if status is not None:
            stmt = stmt.where(CampaignModel.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()
