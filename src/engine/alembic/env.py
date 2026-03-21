"""
Alembic migration environment — async-compatible.

This env.py supports both:
- Online mode (direct async connection to PostgreSQL)
- Offline mode (generates SQL without connecting)

The database URL is loaded from prophet.config.settings so it always
matches the application's .env configuration.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Ensure engine/ is on sys.path so prophet.* imports work when alembic is
# invoked from the engine/ directory (the normal case).
# ---------------------------------------------------------------------------
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

# Load .env before importing settings
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ENGINE_DIR / ".env", override=False)

# ---------------------------------------------------------------------------
# Import application models so their metadata is available for autogenerate
# ---------------------------------------------------------------------------
from prophet.config import settings  # noqa: E402
from prophet.db.database import Base  # noqa: E402
from prophet.db import models  # noqa: F401 — side-effect: registers all ORM models  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic config object — access to .ini values
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging (if the ini has logging section)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url from our settings so alembic always uses the
# same URL as the running application.
_db_url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", _db_url)

# Metadata object that autogenerate compares against the live DB schema
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline migrations (--sql flag)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations without connecting to the database.

    Outputs SQL statements to stdout / a file for review.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Include schema comparisons for JSONB, indexes, etc.
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (async)
# ---------------------------------------------------------------------------


def do_run_migrations(connection: Connection) -> None:
    """Called inside run_async_migrations; executes the actual migration."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Render item kind in revision autogen comments
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations in a sync wrapper."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online (connected) migration execution."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
