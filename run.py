"""
Application entry point.

Starts both the admin bot and the background worker in a single process.
Uses asyncio to run them concurrently.
"""

import asyncio
import signal
import sys

from sqlalchemy import text

from src.core.config import get_settings
from src.core.logging import get_logger, setup_logging
from src.db.base import close_engine, get_engine

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

    # TODO: Start admin bot and worker here (Steps 5-8)
    log.info("application_ready", message="Bot and worker will be added in next steps")

    # Keep running until signal
    stop_event = asyncio.Event()

    def handle_signal() -> None:
        log.info("shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        await close_engine()
        log.info("application_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
