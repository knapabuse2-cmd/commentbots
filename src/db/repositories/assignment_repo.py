"""
Assignment repository — manages account↔channel bindings.

Core queries for the worker:
- Get active assignments for a campaign
- Find free channel for an account
- Mark assignment as blocked (permanent ban)
- Update runtime state (post_id, comment_id, etc.)
"""

import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.assignment import AssignmentModel, AssignmentStatus
from src.db.repositories.base_repo import BaseRepository


class AssignmentRepository(BaseRepository[AssignmentModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AssignmentModel)

    async def get_active_for_campaign(
        self, campaign_id: uuid.UUID
    ) -> list[AssignmentModel]:
        """
        Get all active assignments for a campaign with account and channel loaded.

        This is the main worker query — called every cycle.
        """
        stmt = (
            select(AssignmentModel)
            .where(
                AssignmentModel.campaign_id == campaign_id,
                AssignmentModel.status == AssignmentStatus.ACTIVE,
            )
            .options(
                selectinload(AssignmentModel.account),
                selectinload(AssignmentModel.channel),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_account_and_campaign(
        self,
        account_id: uuid.UUID,
        campaign_id: uuid.UUID,
        *,
        status: AssignmentStatus | None = None,
    ) -> list[AssignmentModel]:
        """Get assignments for a specific account in a campaign."""
        stmt = select(AssignmentModel).where(
            AssignmentModel.account_id == account_id,
            AssignmentModel.campaign_id == campaign_id,
        )
        if status is not None:
            stmt = stmt.where(AssignmentModel.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_for_account(
        self, account_id: uuid.UUID
    ) -> AssignmentModel | None:
        """Get the single active assignment for an account (if any)."""
        stmt = (
            select(AssignmentModel)
            .where(
                AssignmentModel.account_id == account_id,
                AssignmentModel.status == AssignmentStatus.ACTIVE,
            )
            .options(
                selectinload(AssignmentModel.channel),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_blocked(self, assignment_id: uuid.UUID) -> None:
        """
        Mark assignment as permanently blocked (ban/kick/mute).
        The account will never return to this channel.
        """
        await self.update_by_id(
            assignment_id,
            status=AssignmentStatus.BLOCKED,
        )

    async def mark_completed(self, assignment_id: uuid.UUID) -> None:
        """Mark assignment as completed (account moved to another channel)."""
        await self.update_by_id(
            assignment_id,
            status=AssignmentStatus.COMPLETED,
        )

    async def update_state(
        self, assignment_id: uuid.UUID, state_updates: dict
    ) -> None:
        """
        Merge updates into assignment's state JSON.

        Args:
            assignment_id: The assignment to update.
            state_updates: Dict of fields to merge into state.
                e.g. {"current_post_id": 123, "last_comment_at": "2024-01-01T00:00:00"}
        """
        assignment = await self.get_by_id(assignment_id)
        if assignment is None:
            return
        current_state = dict(assignment.state) if assignment.state else {}
        current_state.update(state_updates)
        assignment.state = current_state
        await self.session.flush()

    async def increment_fail_count(self, assignment_id: uuid.UUID) -> int:
        """Increment failure counter and return new value."""
        assignment = await self.get_by_id(assignment_id)
        if assignment is None:
            return 0
        assignment.fail_count += 1
        await self.session.flush()
        return assignment.fail_count

    async def is_channel_occupied(self, channel_id: uuid.UUID) -> bool:
        """Check if a channel already has an active assignment."""
        stmt = select(func.count()).select_from(AssignmentModel).where(
            AssignmentModel.channel_id == channel_id,
            AssignmentModel.status == AssignmentStatus.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0

    async def was_account_blocked_in_channel(
        self, account_id: uuid.UUID, channel_id: uuid.UUID
    ) -> bool:
        """Check if this account was previously blocked in this channel."""
        stmt = select(func.count()).select_from(AssignmentModel).where(
            AssignmentModel.account_id == account_id,
            AssignmentModel.channel_id == channel_id,
            AssignmentModel.status == AssignmentStatus.BLOCKED,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0

    async def count_active_for_campaign(self, campaign_id: uuid.UUID) -> int:
        """Count active assignments in a campaign."""
        stmt = select(func.count()).select_from(AssignmentModel).where(
            AssignmentModel.campaign_id == campaign_id,
            AssignmentModel.status == AssignmentStatus.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
