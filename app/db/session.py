"""Database engine and session lifecycle.

This module owns the connection to PostgreSQL. Nothing else in the
application should create engines or sessions directly.
"""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine() -> AsyncEngine | None:
    """Create the engine and session factory.

    Called once during application startup. Returns None when no
    DATABASE_URL is configured, which keeps the app runnable before
    PostgreSQL exists.
    """
    global _engine, _session_factory

    if not settings.database_url:
        logger.warning(
            "DATABASE_URL is not configured. Database features are disabled."
        )
        return None

    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,
        )
        logger.info("Database engine created.")

    return _engine


async def dispose_engine() -> None:
    """Close all pooled connections during application shutdown."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session per request."""
    if _session_factory is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL in your .env file."
        )

    async with _session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session for code outside the HTTP request cycle.

    Telegram handlers are not FastAPI routes, so they cannot use
    Depends(get_session). They use this instead: one session per
    update, committed on success, rolled back on any exception.

    Committing here rather than inside services keeps a single update
    atomic. A handler that writes a CV row and then advances the
    onboarding state either does both or neither, so a user can never
    end up past a step whose work failed.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL in your .env file."
        )

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database_connection() -> bool:
    """Return True when the database answers a trivial query."""
    if _engine is None:
        return False

    try:
        async with _engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError as exc:
        logger.error("Database health check failed: %s", exc)
        return False
