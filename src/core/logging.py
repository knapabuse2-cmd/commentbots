"""
Structured logging setup with structlog.

Provides human-readable output in dev mode and JSON in production.
Every log line includes: timestamp, level, logger name, and context fields.
"""

import logging
import sys

import structlog


def setup_logging(level: str = "DEBUG", pretty: bool = True) -> None:
    """
    Configure structlog + stdlib logging for the entire application.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        pretty: If True, use colorful console output. If False, use JSON.
    """
    log_level = getattr(logging, level.upper(), logging.DEBUG)

    # Shared processors for both structlog and stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if pretty:
        # Human-readable colored output for development
        renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )
    else:
        # JSON output for production (machine-parseable)
        renderer = structlog.processors.JSONRenderer()  # type: ignore[assignment]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging (for third-party libraries)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=log_level,
        stream=sys.stdout,
        force=True,
    )

    # Silence noisy libraries
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a named logger with structlog.

    Usage:
        log = get_logger(__name__)
        log.info("something happened", account_id=123, channel="@test")
    """
    return structlog.get_logger(name)
