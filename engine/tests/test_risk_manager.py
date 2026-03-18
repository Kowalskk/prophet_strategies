"""
Tests for RiskManager.

Uses in-memory SQLite via the db_session fixture from conftest.py.
RiskManager reads real Position rows — so tests that exercise DB checks
insert real rows into the SQLite session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from prophet.core.risk_manager import RiskManager
from prophet.db.models import Market, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _add_market(db, market_id: int = 1) -> Market:
    market = Market(
        id=market_id,
        condition_id=f"0xtest{market_id}",
        question=f"Test market {market_id}",
        crypto="BTC",
        token_id_yes=f"0xtest{market_id}_yes",
        token_id_no=f"0xtest{market_id}_no",
        status="active",
    )
    db.add(market)
    await db.flush()
    return market


async def _add_position(
    db,
    market_id: int = 1,
    status: str = "open",
    size_usd: float = 50.0,
    net_pnl: float | None = None,
    closed_at: datetime | None = None,
) -> Position:
    pos = Position(
        market_id=market_id,
        strategy="test_strategy",
        side="YES",
        entry_price=0.30,
        size_usd=size_usd,
        shares=size_usd / 0.30,
        status=status,
        opened_at=_utcnow(),
        net_pnl=net_pnl,
        closed_at=closed_at or (_utcnow() if status == "closed" else None),
    )
    db.add(pos)
    await db.flush()
    return pos


def _make_signal(market_id: int = 1, size_usd: float = 50.0, strategy: str = "test"):
    from unittest.mock import MagicMock
    sig = MagicMock()
    sig.market_id = market_id
    sig.size_usd = size_usd
    sig.strategy = strategy
    return sig


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRiskManager:
    async def test_kill_switch_blocks_all(self, db_session):
        """When kill_switch=True, every signal is rejected."""
        rm = RiskManager(db_session)
        signal = _make_signal()

        with patch("prophet.core.risk_manager.settings") as mock_settings:
            mock_settings.kill_switch = True
            mock_settings.paper_trading = True
            mock_settings.max_daily_loss = 200.0
            mock_settings.max_open_positions = 20
            mock_settings.max_position_per_market = 100.0
            mock_settings.max_concentration = 0.25
            mock_settings.max_drawdown_total = 0.30
            mock_settings.target_cryptos = ["BTC", "ETH", "SOL"]

            approved, reason = await rm.check(signal)

        assert approved is False
        assert "Kill switch" in reason or "kill" in reason.lower()

    async def test_daily_loss_limit(self, db_session):
        """If today's net P&L is below -max_daily_loss, trading is blocked."""
        await _add_market(db_session)
        # A closed position with a large loss today
        await _add_position(db_session, status="closed", net_pnl=-250.0, closed_at=_utcnow())

        rm = RiskManager(db_session)
        signal = _make_signal()

        with patch("prophet.core.risk_manager.settings") as mock_settings:
            mock_settings.kill_switch = False
            mock_settings.paper_trading = True
            mock_settings.max_daily_loss = 200.0
            mock_settings.max_open_positions = 20
            mock_settings.max_position_per_market = 100.0
            mock_settings.max_concentration = 0.25
            mock_settings.max_drawdown_total = 0.30
            mock_settings.target_cryptos = ["BTC", "ETH", "SOL"]

            approved, reason = await rm.check(signal)

        assert approved is False
        assert "daily" in reason.lower() or "loss" in reason.lower()

    async def test_open_positions_limit(self, db_session):
        """Reaching max_open_positions blocks new signals."""
        await _add_market(db_session)
        # Create exactly max_open_positions open positions
        for i in range(5):
            await _add_position(db_session, status="open")

        rm = RiskManager(db_session)
        signal = _make_signal()

        with patch("prophet.core.risk_manager.settings") as mock_settings:
            mock_settings.kill_switch = False
            mock_settings.paper_trading = True
            mock_settings.max_daily_loss = 200.0
            mock_settings.max_open_positions = 5  # exactly at limit
            mock_settings.max_position_per_market = 100.0
            mock_settings.max_concentration = 0.25
            mock_settings.max_drawdown_total = 0.30
            mock_settings.target_cryptos = ["BTC", "ETH", "SOL"]

            approved, reason = await rm.check(signal)

        assert approved is False
        assert "position" in reason.lower() or "limit" in reason.lower()

    async def test_approved_signal_passes(self, db_session):
        """With no violations, (True, 'OK') is returned."""
        await _add_market(db_session)
        rm = RiskManager(db_session)
        signal = _make_signal(size_usd=10.0)

        with patch("prophet.core.risk_manager.settings") as mock_settings:
            mock_settings.kill_switch = False
            mock_settings.paper_trading = True
            mock_settings.max_daily_loss = 200.0
            mock_settings.max_open_positions = 20
            mock_settings.max_position_per_market = 100.0
            mock_settings.max_concentration = 0.25
            mock_settings.max_drawdown_total = 0.30
            mock_settings.target_cryptos = ["BTC", "ETH", "SOL"]

            approved, reason = await rm.check(signal)

        assert approved is True
        assert reason == "OK"

    async def test_get_risk_metrics_returns_dict(self, db_session):
        """get_risk_metrics() must return a dict with the expected keys."""
        rm = RiskManager(db_session)

        with patch("prophet.core.risk_manager.settings") as mock_settings:
            mock_settings.kill_switch = False
            mock_settings.paper_trading = True
            mock_settings.max_daily_loss = 200.0
            mock_settings.max_open_positions = 20
            mock_settings.max_position_per_market = 100.0
            mock_settings.max_concentration = 0.25
            mock_settings.max_drawdown_total = 0.30
            mock_settings.target_cryptos = ["BTC", "ETH", "SOL"]

            metrics = await rm.get_risk_metrics()

        required_keys = {
            "kill_switch",
            "paper_trading",
            "daily_loss_pct",
            "open_positions_pct",
            "drawdown_pct",
            "raw",
        }
        assert required_keys.issubset(metrics.keys())
        assert isinstance(metrics["raw"], dict)
        assert "limits" in metrics["raw"]
