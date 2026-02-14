"""
Application entry point.

Starts the admin bot AND the background worker manager concurrently.
Both run in the same asyncio event loop.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from src.core.config import get_settings
from src.core.logging import get_logger, setup_logging
from src.db.base import close_engine, get_engine, get_session_factory

log = get_logger(__name__)


async def main() -> None:
    """Start the application."""
    settings = get_settings()
    setup_logging(level=settings.log_level, pretty=settings.log_pretty)

    # Ensure data directories exist
    Path("data/photos").mkdir(parents=True, exist_ok=True)

    log.info(
        "application_starting",
        version="2.0.0",
        log_level=settings.log_level,
        max_connections=settings.worker_max_connections,
    )

    # Verify database connection (retry up to 30s for Docker networking)
    engine = get_engine()
    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            log.info("database_connection_ok")
            break
        except Exception as e:
            if attempt == max_retries:
                log.error("database_connection_failed", error=str(e), attempts=attempt)
                sys.exit(1)
            log.warning("database_not_ready_retrying", error=str(e), attempt=attempt)
            await asyncio.sleep(3)

    # Create session factory
    session_factory = get_session_factory()

    # Start admin bot
    from src.bot.app import start_bot

    bot, dp = await start_bot(session_factory)

    # Start worker manager (background task)
    from src.worker.manager import WorkerManager

    worker_manager = WorkerManager(session_factory, bot)
    await worker_manager.start()

    log.info("application_ready", workers=worker_manager.get_stats()["running_workers"])

    try:
        # Start polling (blocks until stopped)
        # drop_pending_updates=True skips messages sent while bot was offline
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        # Graceful shutdown
        log.info("application_shutting_down")
        await worker_manager.stop()
        await bot.session.close()
        await close_engine()
        log.info("application_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
