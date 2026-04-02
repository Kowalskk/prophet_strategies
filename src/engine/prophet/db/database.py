"""
Async SQLAlchemy engine and session factory.

Usage
-----
In application startup::

    from prophet.db.database import init_db, close_db

    await init_db()   # Creates engine + (optionally) tables
    ...
    await close_db()  # Disposes pool on shutdown

Anywhere a database session is needed (FastAPI dependency injection)::

    from prophet.db.database import get_session

    async with get_session() as session:
        result = await session.execute(select(Market))
        ...

Or as a FastAPI dependency::

    @router.get("/markets")
    async def list_markets(session: AsyncSession = Depends(get_db)):
        ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from prophet.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# Engine + session factory (module-level singletons, lazy-initialised)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """Create the async engine with sensible pool defaults."""
    return create_async_engine(
        settings.database_url,
        # asyncpg pool settings
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,  # Recycle stale connections automatically
        pool_recycle=3600,   # Recycle connections every hour
        echo=False,          # Set to True for SQL debug output
        future=True,
    )


def get_engine() -> AsyncEngine:
    """Return the module-level engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, creating it on first call."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


# ---------------------------------------------------------------------------
# Context manager for use outside FastAPI (scripts, tests, scheduler tasks)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a database session.

    Automatically commits on clean exit and rolls back on exception.

    Example::

        async with get_session() as session:
            session.add(obj)
            # commit happens automatically
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# FastAPI dependency injection helper
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session per request.

    Usage::

        @router.get("/markets")
        async def list_markets(db: AsyncSession = Depends(get_db)):
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


# ---------------------------------------------------------------------------
# Lifecycle helpers called from prophet.main
# ---------------------------------------------------------------------------


async def init_db(create_tables: bool = False) -> None:
    """Initialise the async engine.

    Parameters
    ----------
    create_tables:
        If True, create all tables defined on :class:`Base` that do not already
        exist.  Useful for development/testing.  In production prefer Alembic
        migrations (``alembic upgrade head``).
    """
    engine = get_engine()
    logger.info("Database engine initialised: %s", settings.database_url.split("@")[-1])

    if create_tables:
        async with engine.begin() as conn:
            # Import models so their metadata is registered on Base
            from prophet.db import models  # noqa: F401
            from prophet.live import live_models  # noqa: F401

            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created (create_tables=True).")


async def close_db() -> None:
    """Dispose the engine connection pool on application shutdown."""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
        logger.info("Database engine disposed.")
