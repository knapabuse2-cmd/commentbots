"""
Channel service — manages channels within campaigns.

Handles:
- Adding channels (single, bulk from text)
- Link parsing and validation
- Channel listing with pagination
- Duplicate detection
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.db.models.channel import ChannelModel, ChannelStatus
from src.db.repositories.channel_repo import ChannelRepository

log = get_logger(__name__)


class ChannelService:
    """Business logic for channel management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ChannelRepository(session)

    async def add_channel(
        self, campaign_id: uuid.UUID, link: str
    ) -> tuple[ChannelModel | None, str | None]:
        """
        Add a single channel to a campaign.

        Returns:
            (channel, error) — channel if added, error string if failed.
        """
        link = link.strip()
        if not link:
            return None, "Empty link"

        # Parse link
        username, invite_hash = ChannelModel.parse_link(link)
        if not username and not invite_hash:
            return None, f"Cannot parse channel link: {link}"

        # Check duplicate
        existing = await self.repo.find_by_link(campaign_id, link)
        if existing:
            return None, f"Channel already exists in campaign: {existing.display_name}"

        channel = await self.repo.create(
            campaign_id=campaign_id,
            link=link,
            username=username,
            invite_hash=invite_hash,
        )

        log.info(
            "channel_added",
            campaign_id=str(campaign_id),
            channel=channel.display_name,
        )
        return channel, None

    async def add_channels_bulk(
        self, campaign_id: uuid.UUID, text: str
    ) -> tuple[int, int, list[str]]:
        """
        Add multiple channels from text (one link per line).

        Args:
            campaign_id: Target campaign.
            text: Multi-line text with channel links.

        Returns:
            (added, skipped, errors) — counts and list of error messages.
        """
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        if not lines:
            return 0, 0, ["No links provided"]

        added = 0
        skipped = 0
        errors: list[str] = []

        for line in lines:
            channel, error = await self.add_channel(campaign_id, line)
            if channel:
                added += 1
            elif error and "already exists" in error:
                skipped += 1
            else:
                errors.append(error or f"Unknown error for: {line}")

        log.info(
            "channels_bulk_added",
            campaign_id=str(campaign_id),
            added=added,
            skipped=skipped,
            errors=len(errors),
        )
        return added, skipped, errors

    async def get_channels(
        self,
        campaign_id: uuid.UUID,
        *,
        status: ChannelStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[ChannelModel]:
        """Get channels in a campaign with pagination."""
        return await self.repo.get_by_campaign(
            campaign_id, status=status, offset=offset, limit=limit
        )

    async def count_channels(
        self, campaign_id: uuid.UUID, status: ChannelStatus | None = None
    ) -> int:
        """Count channels in a campaign."""
        return await self.repo.count_by_campaign(campaign_id, status=status)

    async def remove_channel(self, channel_id: uuid.UUID) -> bool:
        """Remove a channel from campaign."""
        return await self.repo.delete(channel_id)

    async def remove_all_channels(self, campaign_id: uuid.UUID) -> int:
        """Remove all channels from a campaign. Returns count removed."""
        channels = await self.repo.get_by_campaign(campaign_id, limit=10000)
        count = 0
        for ch in channels:
            await self.repo.delete(ch.id)
            count += 1
        return count
