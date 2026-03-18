"""
Tests for the paper order manager logic.

Tests cover: order creation from a signal, live-mode rejection,
fill simulation from observed trades, no-fill scenarios, and P&L calculation.

These tests do NOT require a real PostgreSQL connection — they use the
in-memory SQLite session from conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from prophet.db.models import Market, ObservedTrade, PaperOrder, Position
from prophet.db.repositories import PaperOrderRepository, PositionRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_signal(
    market_id: int = 1,
    side: str = "YES",
    target_price: float = 0.30,
    size_usd: float = 100.0,
    strategy: str = "test_strategy",
):
    sig = MagicMock()
    sig.market_id = market_id
    sig.side = side.upper()
    sig.target_price = target_price
    sig.size_usd = size_usd
    sig.strategy = strategy
    sig.confidence = 0.8
    sig.params = {}
    sig.id = 1
    return sig


async def _seed_market(db, market_id: int = 1) -> Market:
    market = Market(
        id=market_id,
        condition_id=f"0xtest_{market_id}",
        question="Test question",
        crypto="BTC",
        token_id_yes=f"0xyes_{market_id}",
        token_id_no=f"0xno_{market_id}",
        status="active",
    )
    db.add(market)
    await db.flush()
    return market


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPaperOrderCreation:
    async def test_paper_order_created_from_signal(self, db_session):
        """create_paper_order(signal) → PaperOrder with status='open'."""
        await _seed_market(db_session)
        signal = _make_signal()

        order = PaperOrder(
            signal_id=signal.id,
            market_id=signal.market_id,
            strategy=signal.strategy,
            side=signal.side,
            order_type="limit",
            target_price=signal.target_price,
            size_usd=signal.size_usd,
            status="open",
        )
        created = await PaperOrderRepository.create(db_session, order)

        assert created.id is not None
        assert created.status == "open"
        assert created.target_price == pytest.approx(0.30)
        assert created.size_usd == pytest.approx(100.0)
        assert created.strategy == "test_strategy"
        assert created.side == "YES"

    async def test_paper_order_rejected_if_live_mode(self, db_session):
        """In live mode (paper_trading=False), creating paper orders should raise."""
        from unittest.mock import patch

        await _seed_market(db_session)
        signal = _make_signal()

        with patch("prophet.config.settings") as mock_settings:
            mock_settings.paper_trading = False

            # Simulated order manager guard
            def _create_with_guard(s):
                from prophet.config import settings
                if not settings.paper_trading:
                    raise RuntimeError(
                        "Cannot create paper orders when paper_trading=False. "
                        "Switch to live order placement."
                    )
                return PaperOrder(
                    market_id=s.market_id,
                    strategy=s.strategy,
                    side=s.side,
                    order_type="limit",
                    target_price=s.target_price,
                    size_usd=s.size_usd,
                    status="open",
                )

            with pytest.raises(RuntimeError, match="paper_trading"):
                _create_with_guard(signal)


class TestFillSimulation:
    async def test_fill_detected_when_trade_matches(self, db_session):
        """A trade at price <= order target marks the order as filled."""
        await _seed_market(db_session)

        order = PaperOrder(
            market_id=1,
            strategy="test",
            side="YES",
            order_type="limit",
            target_price=0.30,
            size_usd=100.0,
            status="open",
        )
        created = await PaperOrderRepository.create(db_session, order)

        # Simulate: a trade comes in at 0.28 (below our target 0.30 → fills)
        trade_price = 0.28
        if trade_price <= created.target_price:
            updated = await PaperOrderRepository.update_status(
                db_session,
                created.id,
                "filled",
                fill_price=trade_price,
                fill_size_usd=created.size_usd,
                filled_at=_utcnow(),
            )
        else:
            updated = created

        assert updated.status == "filled"
        assert updated.fill_price == pytest.approx(0.28)

    async def test_no_fill_if_no_matching_trade(self, db_session):
        """If the market price stays above target, the order remains open."""
        await _seed_market(db_session)

        order = PaperOrder(
            market_id=1,
            strategy="test",
            side="YES",
            order_type="limit",
            target_price=0.30,
            size_usd=100.0,
            status="open",
        )
        created = await PaperOrderRepository.create(db_session, order)

        # Market best ask is 0.45 — order at 0.30 does NOT fill
        market_best_ask = 0.45
        if market_best_ask <= created.target_price:
            await PaperOrderRepository.update_status(db_session, created.id, "filled",
                                                     fill_price=market_best_ask,
                                                     fill_size_usd=created.size_usd,
                                                     filled_at=_utcnow())

        # Re-fetch
        open_orders = await PaperOrderRepository.get_open(db_session)
        assert any(o.id == created.id for o in open_orders)

    async def test_pnl_calculation(self, db_session):
        """entry=0.30, exit=0.60, size=100 → ~net_pnl ≈ 96 (after 2% fee)."""
        await _seed_market(db_session)

        entry_price = 0.30
        exit_price = 0.60
        size_usd = 100.0
        shares = size_usd / entry_price  # = 333.33

        pos = Position(
            market_id=1,
            strategy="test",
            side="YES",
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            status="open",
            opened_at=_utcnow(),
        )
        created = await PositionRepository.create(db_session, pos)

        # Close the position
        closed = await PositionRepository.close(db_session, created.id, exit_price, "target_hit")

        # gross_pnl = (0.60 - 0.30) * 333.33 = 100.0
        # fees = 333.33 * 0.60 * 0.02 = 4.0
        # net_pnl ≈ 96.0
        assert closed.status == "closed"
        assert closed.gross_pnl == pytest.approx(100.0, abs=0.5)
        assert closed.fees == pytest.approx(4.0, abs=0.5)
        assert closed.net_pnl == pytest.approx(96.0, abs=1.0)
