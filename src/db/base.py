"""
Database engine and session factory.

Uses asyncpg for async PostgreSQL access with connection pooling.
Pool is configured for 100+ concurrent accounts workload.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import get_settings
from src.core.logging import get_logger

log = get_logger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


def create_engine():
    """
    Create async engine with connection pooling tuned for scale.

    Pool settings:
    - pool_size=20: keep 20 persistent connections
    - max_overflow=30: allow up to 50 total (20+30) during spikes
    - pool_timeout=30: wait max 30s for a connection from pool
    - pool_recycle=1800: recycle connections every 30 min (prevent stale)
    """
    settings = get_settings()

    engine = create_async_engine(
        settings.database_url,
        pool_size=20,
        max_overflow=30,
        pool_timeout=30,
        pool_recycle=1800,
        echo=False,  # Set True only for SQL debugging
    )

    log.info(
        "database_engine_created",
        host=settings.postgres_host,
        db=settings.postgres_db,
        pool_size=20,
        max_overflow=30,
    )

    return engine


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create session factory bound to the engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# --- Convenience for dependency injection ---

_engine = None
_session_factory = None


def get_engine():
    """Get or create the global engine singleton."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the global session factory singleton."""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(get_engine())
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async generator that yields a database session.

    Usage:
        async with get_session_factory()() as session:
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    """Dispose the engine and close all connections. Call on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log.info("database_engine_closed")
        _engine = None
        _session_factory = None
