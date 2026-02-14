"""
Channel distributor — assigns channels to accounts evenly.

Core algorithm:
1. Get all channels in campaign that have no active assignment.
2. Get all active accounts assigned to the campaign.
3. Distribute channels round-robin: account gets 1 channel at a time.
4. When account finishes/gets banned from a channel → assign next free one.

Constraint: each channel can only have ONE active assignment (enforced by DB).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.assignment import AssignmentModel, AssignmentStatus
from src.db.models.channel import ChannelStatus
from src.db.repositories.assignment_repo import AssignmentRepository
from src.db.repositories.channel_repo import ChannelRepository

log = get_logger(__name__)


class DistributorService:
    """Distributes channels among accounts for a campaign."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.assignment_repo = AssignmentRepository(session)
        self.channel_repo = ChannelRepository(session)

    async def distribute_initial(
        self,
        campaign_id: uuid.UUID,
        account_ids: list[uuid.UUID],
    ) -> int:
        """
        Initial distribution: assign one free channel to each account.

        Called when campaign starts or when accounts are added.
        Each account gets ONE channel to work on.

        Args:
            campaign_id: The campaign.
            account_ids: List of account UUIDs to assign channels to.

        Returns:
            Number of assignments created.
        """
        # Get free channels (not occupied by any account)
        free_channels = await self.channel_repo.get_free_channels(campaign_id)

        if not free_channels:
            log.warning("no_free_channels", campaign_id=str(campaign_id))
            return 0

        assigned = 0
        channel_idx = 0

        for account_id in account_ids:
            if channel_idx >= len(free_channels):
                break  # No more free channels

            # Check if account already has an active assignment in this campaign
            existing = await self.assignment_repo.get_by_account_and_campaign(
                account_id, campaign_id, status=AssignmentStatus.ACTIVE
            )
            if existing:
                continue  # Already has a channel

            channel = free_channels[channel_idx]

            # Double-check channel is not occupied (race condition safety)
            if await self.assignment_repo.is_channel_occupied(channel.id):
                channel_idx += 1
                continue

            await self.assignment_repo.create(
                campaign_id=campaign_id,
                account_id=account_id,
                channel_id=channel.id,
                status=AssignmentStatus.ACTIVE,
                state={},
            )
            assigned += 1
            channel_idx += 1

            log.debug(
                "channel_assigned",
                account_id=str(account_id)[:8],
                channel=channel.display_name,
            )

        log.info(
            "distribution_complete",
            campaign_id=str(campaign_id),
            assigned=assigned,
            free_remaining=len(free_channels) - channel_idx,
        )
        return assigned

    async def assign_next_channel(
        self,
        campaign_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AssignmentModel | None:
        """
        Find and assign the next free channel to an account.

        Called when an account's current channel is blocked/done.
        Excludes channels where this account was previously blocked.

        Returns:
            New assignment, or None if no free channels available.
        """
        free_channels = await self.channel_repo.get_free_channels(
            campaign_id, exclude_account_id=account_id
        )

        if not free_channels:
            log.warning(
                "no_free_channels_for_account",
                campaign_id=str(campaign_id),
                account_id=str(account_id)[:8],
            )
            return None

        # Take the first free channel
        channel = free_channels[0]

        # Final check — channel not occupied
        if await self.assignment_repo.is_channel_occupied(channel.id):
            # Extremely unlikely but possible race condition
            log.warning("channel_occupied_race", channel_id=str(channel.id)[:8])
            return None

        assignment = await self.assignment_repo.create(
            campaign_id=campaign_id,
            account_id=account_id,
            channel_id=channel.id,
            status=AssignmentStatus.ACTIVE,
            state={},
        )

        log.info(
            "next_channel_assigned",
            account_id=str(account_id)[:8],
            channel=channel.display_name,
        )
        return assignment

    async def get_distribution_stats(
        self, campaign_id: uuid.UUID
    ) -> dict:
        """
        Get distribution statistics for a campaign.

        Returns:
            {
                "total_channels": int,
                "assigned_channels": int,
                "free_channels": int,
                "blocked_channels": int,
                "active_assignments": int,
            }
        """
        total = await self.channel_repo.count_by_campaign(campaign_id)
        active = await self.assignment_repo.count_active_for_campaign(campaign_id)
        free = len(await self.channel_repo.get_free_channels(campaign_id))
        blocked = await self.channel_repo.count_by_campaign(
            campaign_id, status=ChannelStatus.NO_ACCESS
        )

        return {
            "total_channels": total,
            "assigned_channels": active,
            "free_channels": free,
            "blocked_channels": blocked,
            "active_assignments": active,
        }
