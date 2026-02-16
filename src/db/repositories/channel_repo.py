"""
Channel repository — CRUD + queries for free/active channels.

Optimized for 300+ channels per campaign.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.assignment import AssignmentModel, AssignmentStatus
from src.db.models.channel import ChannelModel, ChannelStatus
from src.db.repositories.base_repo import BaseRepository


class ChannelRepository(BaseRepository[ChannelModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChannelModel)

    async def get_by_campaign(
        self,
        campaign_id: uuid.UUID,
        *,
        status: ChannelStatus | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ChannelModel]:
        """Get channels in a campaign, optionally filtered by status."""
        stmt = (
            select(ChannelModel)
            .where(ChannelModel.campaign_id == campaign_id)
            .offset(offset)
            .limit(limit)
            .order_by(ChannelModel.created_at)
        )
        if status is not None:
            stmt = stmt.where(ChannelModel.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_free_channels(
        self,
        campaign_id: uuid.UUID,
        *,
        exclude_account_id: uuid.UUID | None = None,
    ) -> list[ChannelModel]:
        """
        Get channels that have NO active assignment (not occupied by any account).

        Optionally exclude channels where a specific account was previously blocked.

        This is the core query for channel rotation:
        when an account needs a new channel, find one that's free.
        """
        # Subquery: channels that have active assignments in THIS campaign
        occupied_subq = (
            select(AssignmentModel.channel_id)
            .where(
                AssignmentModel.campaign_id == campaign_id,
                AssignmentModel.status == AssignmentStatus.ACTIVE,
            )
            .subquery()
        )

        stmt = (
            select(ChannelModel)
            .where(
                ChannelModel.campaign_id == campaign_id,
                ChannelModel.status.in_([ChannelStatus.PENDING, ChannelStatus.ACTIVE]),
                ChannelModel.id.notin_(select(occupied_subq)),
            )
            .order_by(ChannelModel.created_at)
        )

        # If we know which account is looking, also exclude channels
        # where this account was previously blocked
        if exclude_account_id is not None:
            blocked_subq = (
                select(AssignmentModel.channel_id)
                .where(
                    AssignmentModel.account_id == exclude_account_id,
                    AssignmentModel.status == AssignmentStatus.BLOCKED,
                )
                .subquery()
            )
            stmt = stmt.where(ChannelModel.id.notin_(select(blocked_subq)))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_campaign(
        self, campaign_id: uuid.UUID, status: ChannelStatus | None = None
    ) -> int:
        """Count channels in a campaign, optionally by status."""
        stmt = select(func.count()).select_from(ChannelModel).where(
            ChannelModel.campaign_id == campaign_id
        )
        if status is not None:
            stmt = stmt.where(ChannelModel.status == status)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def find_by_link(
        self, campaign_id: uuid.UUID, link: str
    ) -> ChannelModel | None:
        """Check if a channel link already exists in a campaign (avoid duplicates)."""
        username, invite_hash = ChannelModel.parse_link(link)
        stmt = select(ChannelModel).where(ChannelModel.campaign_id == campaign_id)

        if username:
            stmt = stmt.where(ChannelModel.username == username)
        elif invite_hash:
            stmt = stmt.where(ChannelModel.invite_hash == invite_hash)
        else:
            # Fallback: compare raw link
            stmt = stmt.where(ChannelModel.link == link)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def bulk_create_from_links(
        self, campaign_id: uuid.UUID, links: list[str]
    ) -> tuple[int, int]:
        """
        Bulk import channels from a list of links.
        Skips duplicates within the campaign.

        Returns:
            (added, skipped) — count of new and duplicate channels.
        """
        added = 0
        skipped = 0

        for link in links:
            link = link.strip()
            if not link:
                continue

            # Check duplicate
            existing = await self.find_by_link(campaign_id, link)
            if existing is not None:
                skipped += 1
                continue

            username, invite_hash = ChannelModel.parse_link(link)
            await self.create(
                campaign_id=campaign_id,
                link=link,
                username=username,
                invite_hash=invite_hash,
            )
            added += 1

        return added, skipped
