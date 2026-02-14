"""
Bot application — creates and configures the aiogram bot instance.

Registers:
- Middlewares (database session, auth)
- All handler routers
- Startup/shutdown hooks
"""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.bot.handlers import accounts, bulk_import, campaigns, proxy, settings, start, stats
from src.bot.middlewares.auth import AuthMiddleware
from src.bot.middlewares.db_session import DbSessionMiddleware
from src.core.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__)


def create_bot() -> Bot:
    """Create the aiogram Bot instance."""
    settings = get_settings()
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    """
    Create and configure the Dispatcher with all middlewares and routers.

    Args:
        session_factory: SQLAlchemy async session factory for DB access.
    """
    dp = Dispatcher(storage=MemoryStorage())

    # Register middlewares (order matters: db_session first, then auth)
    dp.update.middleware(DbSessionMiddleware(session_factory))
    dp.update.middleware(AuthMiddleware())

    # Register routers (order matters: more specific first)
    dp.include_router(start.router)
    dp.include_router(accounts.router)
    dp.include_router(bulk_import.router)
    dp.include_router(campaigns.router)
    dp.include_router(proxy.router)
    dp.include_router(stats.router)
    dp.include_router(settings.router)

    log.info("dispatcher_configured", routers=7, middlewares=2)
    return dp


async def start_bot(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Bot, Dispatcher]:
    """
    Create bot and dispatcher, ready to start polling.

    Returns:
        (bot, dispatcher) tuple.
    """
    bot = create_bot()
    dp = create_dispatcher(session_factory)

    # Verify bot token
    me = await bot.get_me()
    log.info("bot_started", username=me.username, bot_id=me.id)

    return bot, dp
