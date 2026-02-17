"""
Channel service — manages channels within campaigns.

Handles:
- Adding channels (single, bulk from text)
- Link parsing and validation
- Channel listing with pagination
- Duplicate detection
"""

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import OwnershipError
from src.core.logging import get_logger
from src.db.models.channel import ChannelModel, ChannelStatus
from src.db.repositories.campaign_repo import CampaignRepository
from src.db.repositories.channel_repo import ChannelRepository

log = get_logger(__name__)


class ChannelService:
    """Business logic for channel management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ChannelRepository(session)
        self.campaign_repo = CampaignRepository(session)

    async def _verify_campaign_ownership(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None
    ) -> None:
        """Verify campaign belongs to owner (channels don't have direct owner_id)."""
        if owner_id is None:
            return
        campaign = await self.campaign_repo.get_by_id(campaign_id)
        if campaign is None or campaign.owner_id != owner_id:
            raise OwnershipError("Campaign not found")

    async def add_channel(
        self, campaign_id: uuid.UUID, link: str,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[ChannelModel | None, str | None]:
        """
        Add a single channel to a campaign.

        Returns:
            (channel, error) — channel if added, error string if failed.
        """
        await self._verify_campaign_ownership(campaign_id, owner_id)
        link = link.strip()
        if not link:
            return None, "Empty link"

        # Parse link
        username, invite_hash = ChannelModel.parse_link(link)
        if not username and not invite_hash:
            return None, f"Cannot parse channel link: {link}"

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

        # Append to channels.txt for record-keeping
        self._append_to_channels_file(link)

        return channel, None

    @staticmethod
    def _append_to_channels_file(link: str) -> None:
        """Append channel link to data/channels.txt (one per line, no dupes)."""
        channels_file = Path("data/channels.txt")
        try:
            channels_file.parent.mkdir(parents=True, exist_ok=True)

            # Read existing to avoid duplicates
            existing: set[str] = set()
            if channels_file.exists():
                existing = {
                    line.strip()
                    for line in channels_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }

            if link.strip() not in existing:
                with channels_file.open("a", encoding="utf-8") as f:
                    f.write(link.strip() + "\n")
        except Exception as exc:
            log.warning("channels_file_append_error", error=str(exc), link=link)

    async def add_channels_bulk(
        self, campaign_id: uuid.UUID, text: str,
        owner_id: uuid.UUID | None = None,
    ) -> tuple[int, int, list[str]]:
        """
        Add multiple channels from text (one link per line).

        Args:
            campaign_id: Target campaign.
            text: Multi-line text with channel links.

        Returns:
            (added, skipped, errors) — counts and list of error messages.
        """
        await self._verify_campaign_ownership(campaign_id, owner_id)
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
        owner_id: uuid.UUID | None = None,
    ) -> list[ChannelModel]:
        """Get channels in a campaign with pagination."""
        await self._verify_campaign_ownership(campaign_id, owner_id)
        return await self.repo.get_by_campaign(
            campaign_id, status=status, offset=offset, limit=limit
        )

    async def count_channels(
        self, campaign_id: uuid.UUID, status: ChannelStatus | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> int:
        """Count channels in a campaign."""
        await self._verify_campaign_ownership(campaign_id, owner_id)
        return await self.repo.count_by_campaign(campaign_id, status=status)

    async def remove_channel(
        self, channel_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> bool:
        """Remove a channel from campaign."""
        if owner_id is not None:
            channel = await self.repo.get_by_id(channel_id)
            if channel is None:
                return False
            await self._verify_campaign_ownership(channel.campaign_id, owner_id)
        return await self.repo.delete(channel_id)

    async def remove_all_channels(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> int:
        """Remove all channels from a campaign. Returns count removed."""
        await self._verify_campaign_ownership(campaign_id, owner_id)
        channels = await self.repo.get_by_campaign(campaign_id, limit=10000)
        count = 0
        for ch in channels:
            await self.repo.delete(ch.id)
            count += 1
        return count
