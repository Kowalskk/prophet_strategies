#!/usr/bin/env python3
"""
setup_db.py — Initialize the Prophet PostgreSQL database.

This script is IDEMPOTENT: running it multiple times is safe.

What it does:
1. Reads configuration from .env (or environment variables).
2. Connects to PostgreSQL using the DATABASE_URL.
3. Creates all 9 tables (via SQLAlchemy metadata).
4. Seeds default strategy_configs rows for all 3 strategies.
5. Seeds initial system_state rows.
6. Prints a summary of everything created/skipped.

Usage (from the engine/ directory):
    python scripts/setup_db.py                    # Uses .env in cwd
    DATABASE_URL=postgresql+asyncpg://... python scripts/setup_db.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure engine/ is on sys.path so prophet.* imports work
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(ENGINE_DIR))

# Load .env before importing prophet.config
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env", override=False)

# ---------------------------------------------------------------------------
# Prophet imports (after path + dotenv setup)
# ---------------------------------------------------------------------------
from sqlalchemy import text, select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession  # noqa: E402

from prophet.config import settings  # noqa: E402
from prophet.db.database import Base  # noqa: E402
from prophet.db import models  # noqa: F401 — registers all ORM models on Base  # noqa: E402
from prophet.db.models import SystemState, StrategyConfig  # noqa: E402
from prophet.strategies.registry import list_strategies  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("setup_db")

# ---------------------------------------------------------------------------
# Default system_state rows
# ---------------------------------------------------------------------------

SYSTEM_STATE_DEFAULTS: list[tuple[str, dict]] = [
    ("kill_switch",       {"enabled": False}),
    ("paper_trading",     {"enabled": True}),
    ("last_scan_at",      {"timestamp": None}),
    ("daily_pnl",         {"usd": 0.0, "date": None}),
    ("total_pnl",         {"usd": 0.0}),
    ("peak_equity",       {"usd": 0.0}),
    ("engine_version",    {"version": "1.0.0"}),
    ("scan_count",        {"count": 0}),
    ("signal_count",      {"count": 0}),
    ("fill_count",        {"count": 0}),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_tables(engine) -> int:
    """Create all tables that don't already exist. Returns count of new tables."""
    logger.info("Creating tables (CREATE TABLE IF NOT EXISTS)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Count tables now present
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        count = result.scalar_one()

    logger.info("Tables present in database: %d", count)
    return count


async def seed_strategy_configs(session: AsyncSession) -> dict[str, int]:
    """Insert default (global) strategy_configs rows if they don't exist.

    One row per strategy with market_id=NULL and crypto=NULL represents the
    global default parameters for that strategy.
    """
    strategies = list_strategies()
    created = 0
    skipped = 0

    for strategy_info in strategies:
        name = strategy_info["name"]
        default_params = strategy_info["default_params"]

        # Check if a global default already exists
        result = await session.execute(
            select(StrategyConfig).where(
                StrategyConfig.strategy == name,
                StrategyConfig.market_id.is_(None),
                StrategyConfig.crypto.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            logger.info("  strategy_configs: %r — already exists (id=%d), skipping.", name, existing.id)
            skipped += 1
            continue

        cfg = StrategyConfig(
            strategy=name,
            market_id=None,
            crypto=None,
            enabled=True,
            params=default_params,
        )
        session.add(cfg)
        logger.info("  strategy_configs: %r — created with default params.", name)
        created += 1

    await session.flush()
    return {"created": created, "skipped": skipped}


async def seed_system_state(session: AsyncSession) -> dict[str, int]:
    """Insert system_state rows that don't already exist."""
    created = 0
    skipped = 0

    for key, value in SYSTEM_STATE_DEFAULTS:
        result = await session.execute(
            select(SystemState).where(SystemState.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            logger.info("  system_state: %r — already exists, skipping.", key)
            skipped += 1
            continue

        row = SystemState(key=key, value=value)
        session.add(row)
        logger.info("  system_state: %r — created.", key)
        created += 1

    await session.flush()
    return {"created": created, "skipped": skipped}


async def verify_connection(engine) -> None:
    """Quick connectivity test; raises on failure."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version()"))
        pg_version = result.scalar_one()
    logger.info("Connected to PostgreSQL: %s", pg_version.split(",")[0])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    db_url = settings.database_url
    # Mask password in log output
    safe_url = db_url.split("@")[-1] if "@" in db_url else db_url
    logger.info("Connecting to: %s", safe_url)

    engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        # Short pool for a one-shot script
        pool_size=2,
        max_overflow=0,
    )

    try:
        # 1. Verify connectivity
        await verify_connection(engine)

        # 2. Create tables
        table_count = await create_tables(engine)

        # 3. Seed data inside a single transaction
        session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=True,
            autocommit=False,
        )

        async with session_factory() as session:
            async with session.begin():
                logger.info("Seeding strategy_configs...")
                sc_stats = await seed_strategy_configs(session)

                logger.info("Seeding system_state...")
                ss_stats = await seed_system_state(session)

        # 4. Summary
        print()
        print("=" * 60)
        print("  Prophet DB Setup — Summary")
        print("=" * 60)
        print(f"  Database URL   : {safe_url}")
        print(f"  Tables present : {table_count}")
        print()
        print("  strategy_configs:")
        print(f"    Created : {sc_stats['created']}")
        print(f"    Skipped : {sc_stats['skipped']} (already existed)")
        print()
        print("  system_state:")
        print(f"    Created : {ss_stats['created']}")
        print(f"    Skipped : {ss_stats['skipped']} (already existed)")
        print()
        print("  Setup complete. Run 'alembic upgrade head' for future migrations.")
        print("=" * 60)

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
