"""
Auth middleware — auto-creates user record and injects user_id.

Every incoming update gets checked:
1. Get telegram_id from the update.
2. Find or create UserModel in database.
3. Inject user's UUID as "owner_id" into handler data.

This ensures every handler has access to the current user's ID
without manually querying the database.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from src.core.logging import get_logger
from src.db.repositories.user_repo import UserRepository

log = get_logger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Automatically registers users and injects owner_id."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Extract telegram user from any event type
        user = None
        if isinstance(event, Update):
            if event.message:
                user = event.message.from_user
            elif event.callback_query:
                user = event.callback_query.from_user
        elif hasattr(event, "from_user"):
            user = event.from_user

        if user is None:
            return await handler(event, data)

        # Get or create user record
        session = data.get("session")
        if session is None:
            return await handler(event, data)

        repo = UserRepository(session)
        db_user, created = await repo.get_or_create(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        if created:
            log.info("new_user_registered", telegram_id=user.id, username=user.username)

        # Inject owner_id for all handlers
        data["owner_id"] = db_user.id
        data["db_user"] = db_user

        return await handler(event, data)
