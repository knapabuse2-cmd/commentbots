"""
Campaign service — manages campaign lifecycle and message templates.

Handles:
- Campaign CRUD (create, edit message, start/stop/delete)
- Message storage with entities preservation
- Account assignment to campaigns
- Campaign stats
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import CampaignError, CampaignNoAccountsError, OwnershipError
from src.core.logging import get_logger
from src.db.models.account import AccountModel, AccountStatus
from src.db.models.campaign import CampaignModel, CampaignStatus
from src.db.models.event_log import EventType
from src.db.repositories.account_repo import AccountRepository
from src.db.repositories.campaign_repo import CampaignRepository
from src.db.repositories.event_log_repo import EventLogRepository

log = get_logger(__name__)


class CampaignService:
    """Business logic for campaign management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CampaignRepository(session)
        self.account_repo = AccountRepository(session)
        self.event_repo = EventLogRepository(session)

    # ---- Ownership ----

    async def _verify_ownership(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None
    ) -> CampaignModel:
        """Fetch campaign and verify ownership. Returns campaign or raises."""
        campaign = await self.repo.get_by_id(campaign_id)
        if campaign is None:
            raise OwnershipError("Campaign not found")
        if owner_id is not None and campaign.owner_id != owner_id:
            raise OwnershipError("Campaign not found")
        return campaign

    # ---- CRUD ----

    async def create_campaign(
        self, owner_id: uuid.UUID, name: str
    ) -> CampaignModel:
        """Create a new campaign in DRAFT status."""
        campaign = await self.repo.create(
            owner_id=owner_id,
            name=name,
            status=CampaignStatus.DRAFT,
        )
        log.info("campaign_created", campaign_id=str(campaign.id), name=name)
        return campaign

    async def get_campaign(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> CampaignModel | None:
        """Get campaign by ID (with optional ownership check)."""
        return await self._verify_ownership(campaign_id, owner_id)

    async def get_campaign_details(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> CampaignModel | None:
        """Get campaign with channels and assignments loaded."""
        await self._verify_ownership(campaign_id, owner_id)
        return await self.repo.get_with_details(campaign_id)

    async def get_campaigns(
        self,
        owner_id: uuid.UUID,
        *,
        status: CampaignStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[CampaignModel]:
        """Get campaigns for an owner."""
        return await self.repo.get_by_owner(
            owner_id, status=status, offset=offset, limit=limit
        )

    async def count_campaigns(
        self, owner_id: uuid.UUID, status: CampaignStatus | None = None
    ) -> int:
        """Count campaigns for an owner."""
        return await self.repo.count_by_owner(owner_id, status=status)

    async def delete_campaign(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> bool:
        """Delete a campaign and all related data (channels, assignments)."""
        campaign = await self._verify_ownership(campaign_id, owner_id)
        if campaign.status == CampaignStatus.ACTIVE:
            raise CampaignError("Cannot delete an active campaign — pause it first")
        return await self.repo.delete(campaign_id)

    # ---- Message Template ----

    async def set_message(
        self,
        campaign_id: uuid.UUID,
        text: str,
        entities: list | None = None,
        photo_id: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> CampaignModel | None:
        """
        Set the campaign message template.

        Args:
            text: Message text.
            entities: Telegram MessageEntity list as dicts (serialized).
                      Stored as JSONB, reconstructed into Telethon objects by worker.
            photo_id: Telegram file_id for attached photo (if any).
            owner_id: If provided, verifies the campaign belongs to this user.
        """
        await self._verify_ownership(campaign_id, owner_id)
        campaign = await self.repo.update_by_id(
            campaign_id,
            message_text=text,
            message_entities=entities,
            message_photo_id=photo_id,
        )
        if campaign:
            log.info(
                "campaign_message_set",
                campaign_id=str(campaign_id),
                has_photo=photo_id is not None,
                has_entities=entities is not None,
                text_length=len(text),
            )
        return campaign

    # ---- Lifecycle ----

    async def start_campaign(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID
    ) -> CampaignModel:
        """
        Start a campaign — sets status to ACTIVE.

        Validates that campaign has a message, channels, and accounts.
        """
        await self._verify_ownership(campaign_id, owner_id)
        campaign = await self.repo.get_with_details(campaign_id)
        if campaign is None:
            raise CampaignError("Campaign not found")

        if not campaign.message_text:
            raise CampaignError("Campaign has no message — set a message first")

        if not campaign.channels:
            raise CampaignError("Campaign has no channels — add channels first")

        if not campaign.assignments:
            raise CampaignError("Campaign has no accounts — add accounts first")

        campaign.status = CampaignStatus.ACTIVE
        await self.session.flush()

        await self.event_repo.log_event(
            owner_id=owner_id,
            event_type=EventType.CAMPAIGN_STARTED,
            message=f"Campaign '{campaign.name}' started with {len(campaign.channels)} channels and {len(campaign.assignments)} accounts",
            campaign_id=campaign.id,
        )

        log.info(
            "campaign_started",
            campaign_id=str(campaign_id),
            channels=len(campaign.channels),
            accounts=len(campaign.assignments),
        )
        return campaign

    async def pause_campaign(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID
    ) -> CampaignModel | None:
        """Pause a running campaign."""
        await self._verify_ownership(campaign_id, owner_id)
        campaign = await self.repo.update_by_id(
            campaign_id, status=CampaignStatus.PAUSED
        )
        if campaign:
            await self.event_repo.log_event(
                owner_id=owner_id,
                event_type=EventType.CAMPAIGN_PAUSED,
                message=f"Campaign '{campaign.name}' paused",
                campaign_id=campaign.id,
            )
        return campaign

    async def complete_campaign(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID
    ) -> CampaignModel | None:
        """Mark campaign as completed."""
        await self._verify_ownership(campaign_id, owner_id)
        campaign = await self.repo.update_by_id(
            campaign_id, status=CampaignStatus.COMPLETED
        )
        if campaign:
            await self.event_repo.log_event(
                owner_id=owner_id,
                event_type=EventType.CAMPAIGN_COMPLETED,
                message=f"Campaign '{campaign.name}' completed",
                campaign_id=campaign.id,
            )
        return campaign

    # ---- Account Assignment ----

    async def get_campaign_accounts(
        self, campaign_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> list[AccountModel]:
        """Get all accounts assigned to a campaign via assignments."""
        await self._verify_ownership(campaign_id, owner_id)
        campaign = await self.repo.get_with_details(campaign_id)
        if campaign is None:
            return []

        account_ids = {a.account_id for a in campaign.assignments}
        accounts = []
        for aid in account_ids:
            acc = await self.account_repo.get_by_id(aid)
            if acc:
                accounts.append(acc)
        return accounts
