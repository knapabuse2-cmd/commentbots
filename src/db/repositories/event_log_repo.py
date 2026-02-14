"""
Event log repository — write events + query for stats/notifications.

Optimized for high write throughput (worker logs every action).
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.event_log import EventLogModel, EventType
from src.db.repositories.base_repo import BaseRepository


class EventLogRepository(BaseRepository[EventLogModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EventLogModel)

    async def log_event(
        self,
        owner_id: uuid.UUID,
        event_type: EventType,
        message: str,
        *,
        campaign_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
        details: dict | None = None,
    ) -> EventLogModel:
        """Create a new event log entry."""
        return await self.create(
            owner_id=owner_id,
            event_type=event_type,
            message=message,
            campaign_id=campaign_id,
            account_id=account_id,
            channel_id=channel_id,
            details=details,
        )

    async def get_recent(
        self,
        owner_id: uuid.UUID,
        *,
        event_type: EventType | None = None,
        campaign_id: uuid.UUID | None = None,
        hours: int = 24,
        limit: int = 50,
    ) -> list[EventLogModel]:
        """Get recent events for an owner, optionally filtered."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        stmt = (
            select(EventLogModel)
            .where(
                EventLogModel.owner_id == owner_id,
                EventLogModel.created_at >= since,
            )
            .order_by(EventLogModel.created_at.desc())
            .limit(limit)
        )

        if event_type is not None:
            stmt = stmt.where(EventLogModel.event_type == event_type)
        if campaign_id is not None:
            stmt = stmt.where(EventLogModel.campaign_id == campaign_id)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_type(
        self,
        owner_id: uuid.UUID,
        event_type: EventType,
        *,
        hours: int = 24,
    ) -> int:
        """Count events of a specific type in the last N hours."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        stmt = (
            select(func.count())
            .select_from(EventLogModel)
            .where(
                EventLogModel.owner_id == owner_id,
                EventLogModel.event_type == event_type,
                EventLogModel.created_at >= since,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_stats_summary(
        self, owner_id: uuid.UUID, *, hours: int = 24
    ) -> dict[str, int]:
        """
        Get event counts grouped by type for the last N hours.

        Returns:
            {"comment_posted": 42, "account_banned": 1, ...}
        """
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        stmt = (
            select(EventLogModel.event_type, func.count())
            .where(
                EventLogModel.owner_id == owner_id,
                EventLogModel.created_at >= since,
            )
            .group_by(EventLogModel.event_type)
        )
        result = await self.session.execute(stmt)
        return {row[0].value: row[1] for row in result.all()}
