"""
pytest configuration and shared fixtures for Prophet engine tests.

All tests use an in-memory async SQLite session (aiosqlite) so no real
PostgreSQL connection is needed.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from prophet.db.database import Base


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for all async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory SQLite database
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session.  Tables are created fresh for each test."""
    # Import models so they register on Base.metadata
    from prophet.db import models  # noqa: F401

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        yield session

    # Drop all tables and dispose engine after each test
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Mock Polymarket CLOB client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_clob_client():
    """Mock of PolymarketClient with hardcoded responses."""
    client = MagicMock()
    client.get_order_book = AsyncMock(
        return_value={
            "bids": [{"price": 0.48, "size": 500}, {"price": 0.45, "size": 1000}],
            "asks": [{"price": 0.52, "size": 300}, {"price": 0.55, "size": 800}],
        }
    )
    client.get_trades = AsyncMock(
        return_value=[
            {"price": 0.50, "size": 100, "side": "YES", "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
    )
    client.place_order = AsyncMock(return_value={"order_id": "test-order-123", "status": "open"})
    return client


# ---------------------------------------------------------------------------
# Sample market fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_market():
    """A Market-like object for BTC > 100000 prediction."""
    market = MagicMock()
    market.id = 1
    market.condition_id = "0xabc123"
    market.question = "Will BTC be above $100,000 by end of week?"
    market.crypto = "BTC"
    market.threshold = 100_000.0
    market.direction = "ABOVE"
    market.resolution_date = date(2026, 3, 31)
    market.token_id_yes = "0xabc123_yes"
    market.token_id_no = "0xabc123_no"
    market.status = "active"
    market.resolved_outcome = None
    return market


# ---------------------------------------------------------------------------
# Sample order book fixture (spread ~5%)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_orderbook():
    """OrderBook fixture with a ~5% spread around mid=0.50."""

    def _make_side(best_bid: float, best_ask: float, depth: float = 500.0) -> MagicMock:
        side = MagicMock()
        side.best_bid = best_bid
        side.best_ask = best_ask
        side.mid_price = (best_bid + best_ask) / 2.0
        side.bid_depth_10pct = depth
        side.ask_depth_10pct = depth
        side.spread_pct = (best_ask - best_bid) / best_ask * 100
        side.bids = [MagicMock(price=best_bid, size=500)]
        side.asks = [MagicMock(price=best_ask, size=500)]
        return side

    # YES side: bid=0.475, ask=0.525 → mid=0.50, spread≈9.5%
    # Combined YES_ask + NO_ask = 0.525 + 0.525 = 1.05 (no gap)
    yes_side = _make_side(best_bid=0.475, best_ask=0.525)
    no_side = _make_side(best_bid=0.475, best_ask=0.525)

    return {"yes": yes_side, "no": no_side}
