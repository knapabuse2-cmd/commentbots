"""
Application entry point.

Starts the admin bot (and later the background worker) in a single process.
Uses asyncio to run them concurrently.
"""

import asyncio
import sys

from sqlalchemy import text

from src.core.config import get_settings
from src.core.logging import get_logger, setup_logging
from src.db.base import close_engine, get_engine, get_session_factory

log = get_logger(__name__)


async def main() -> None:
    """Start the application."""
    settings = get_settings()
    setup_logging(level=settings.log_level, pretty=settings.log_pretty)

    log.info(
        "application_starting",
        version="2.0.0",
        log_level=settings.log_level,
        max_connections=settings.worker_max_connections,
    )

    # Verify database connection
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("database_connection_ok")
    except Exception as e:
        log.error("database_connection_failed", error=str(e))
        sys.exit(1)

    # Create session factory
    session_factory = get_session_factory()

    # Start admin bot
    from src.bot.app import start_bot

    bot, dp = await start_bot(session_factory)

    log.info("application_ready")

    try:
        # Start polling (blocks until stopped)
        # drop_pending_updates=True skips messages sent while bot was offline
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await bot.session.close()
        await close_engine()
        log.info("application_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
