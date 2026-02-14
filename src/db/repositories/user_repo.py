"""
User repository — CRUD + lookup by telegram_id.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.user import UserModel
from src.db.repositories.base_repo import BaseRepository


class UserRepository(BaseRepository[UserModel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserModel)

    async def get_by_telegram_id(self, telegram_id: int) -> UserModel | None:
        """Find user by their Telegram user ID."""
        stmt = select(UserModel).where(UserModel.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> tuple[UserModel, bool]:
        """
        Get existing user or create a new one.

        Returns:
            (user, created) — tuple of user instance and whether it was created.
        """
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None:
            return user, False

        user = await self.create(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        return user, True

    async def update_notification_prefs(
        self, user_id, prefs: dict
    ) -> UserModel | None:
        """Update notification preferences for a user."""
        return await self.update_by_id(user_id, notification_prefs=prefs)
