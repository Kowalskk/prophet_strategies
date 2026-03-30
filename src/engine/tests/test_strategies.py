"""
Tests for the three trading strategies.

Uses mock orderbook from conftest.py — no real DB or network calls needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prophet.strategies.liquidity_sniper import LiquiditySniperStrategy
from prophet.strategies.stink_bid import StinkBidStrategy
from prophet.strategies.volatility_spread import VolatilitySpreadStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_book(best_bid: float | None, best_ask: float | None, depth: float = 500.0) -> MagicMock:
    """Build a minimal order-book mock."""
    book = MagicMock()
    book.best_bid = best_bid
    book.best_ask = best_ask
    book.mid_price = ((best_bid or 0) + (best_ask or 0)) / 2.0 if best_bid and best_ask else None
    book.ask_depth_10pct = depth
    book.bid_depth_10pct = depth
    book.asks = [MagicMock(price=best_ask, size=500)] if best_ask else []
    book.bids = [MagicMock(price=best_bid, size=500)] if best_bid else []
    return book


def _make_market(market_id: int = 1) -> MagicMock:
    market = MagicMock()
    market.id = market_id
    market.crypto = "BTC"
    return market


# ---------------------------------------------------------------------------
# VolatilitySpreadStrategy
# ---------------------------------------------------------------------------


class TestVolatilitySpread:
    @pytest.fixture
    def strategy(self):
        return VolatilitySpreadStrategy()

    async def test_generates_two_signals(self, strategy, sample_market):
        """mid=0.50, spread=5% → two signals near 0.475."""
        yes_book = _make_book(best_bid=0.475, best_ask=0.525)
        no_book = _make_book(best_bid=0.475, best_ask=0.525)
        ob = {"yes": yes_book, "no": no_book}

        params = {
            "spread_percent": 5.0,
            "entry_price_max": 0.97,  # max combined cost (YES+NO); 0.95 combined < 0.97 → passes
            "capital_per_side": 50.0,
        }
        signals = await strategy.evaluate(sample_market, ob, 100_000.0, params)

        assert len(signals) == 2
        sides = {s.side for s in signals}
        assert sides == {"YES", "NO"}

        # Prices should be ~mid * (1 - spread/100) = 0.50 * 0.95 = 0.475
        for sig in signals:
            assert sig.target_price == pytest.approx(0.475, abs=0.01)
            assert sig.size_usd == 50.0

    async def test_skips_if_above_max_price(self, strategy, sample_market):
        """mid=0.10, entry_price_max=0.05 → target=0.095 > max → no signals."""
        yes_book = _make_book(best_bid=0.095, best_ask=0.105)
        ob = {"yes": yes_book, "no": _make_book(best_bid=0.895, best_ask=0.905)}

        params = {
            "spread_percent": 5.0,
            "entry_price_max": 0.05,  # target ≈ 0.095 > 0.05 → skip
            "capital_per_side": 50.0,
        }
        signals = await strategy.evaluate(sample_market, ob, 100_000.0, params)
        assert signals == []

    async def test_no_book_returns_empty(self, strategy, sample_market):
        """Missing YES side → empty list."""
        signals = await strategy.evaluate(sample_market, {}, 100_000.0, {})
        assert signals == []


# ---------------------------------------------------------------------------
# StinkBidStrategy
# ---------------------------------------------------------------------------


class TestStinkBid:
    @pytest.fixture
    def strategy(self):
        return StinkBidStrategy()

    async def test_tier1_and_tier2_signals(self, strategy, sample_market):
        """Normal market → 4 signals (2 tiers × 2 sides)."""
        yes_book = _make_book(best_bid=0.48, best_ask=0.52)
        no_book = _make_book(best_bid=0.48, best_ask=0.52)
        ob = {"yes": yes_book, "no": no_book}

        signals = await strategy.evaluate(sample_market, ob, 100_000.0, {})
        assert len(signals) == 4

        tiers = {s.target_price for s in signals}
        assert 0.03 in tiers   # tier1
        assert 0.005 in tiers  # tier2

        sides = {s.side for s in signals}
        assert sides == {"YES", "NO"}

    async def test_skips_if_market_at_tier_price(self, strategy, sample_market):
        """best_ask=0.03 (equal to tier1) → skip tier1 YES, only tier2 YES remains + both NOs."""
        yes_book = _make_book(best_bid=0.025, best_ask=0.03)  # at tier1 price
        no_book = _make_book(best_bid=0.48, best_ask=0.52)
        ob = {"yes": yes_book, "no": no_book}

        signals = await strategy.evaluate(sample_market, ob, 100_000.0, {})

        # YES tier1 skipped (best_ask == tier1_price → skip), YES tier2 passes
        # NO tier1 passes, NO tier2 passes → 3 signals total
        yes_prices = [s.target_price for s in signals if s.side == "YES"]
        assert 0.03 not in yes_prices
        assert 0.005 in yes_prices

    async def test_no_signals_if_both_sides_at_tier(self, strategy, sample_market):
        """Both sides at/below tier1 → only tier2 (price=0.005) signals survive."""
        yes_book = _make_book(best_bid=0.025, best_ask=0.03)
        no_book = _make_book(best_bid=0.025, best_ask=0.03)
        ob = {"yes": yes_book, "no": no_book}

        signals = await strategy.evaluate(sample_market, ob, 100_000.0, {})
        # Both tier1 (YES and NO) skipped; tier2 (YES and NO) remain → 2 signals
        assert len(signals) == 2
        for s in signals:
            assert s.target_price == 0.005


# ---------------------------------------------------------------------------
# LiquiditySniperStrategy
# ---------------------------------------------------------------------------


class TestLiquiditySniper:
    @pytest.fixture
    def strategy(self):
        return LiquiditySniperStrategy()

    async def test_detects_gap(self, strategy, sample_market):
        """YES_ask=0.47 + NO_ask=0.47 = 0.94 < 0.97 → combined gap → 2 signals."""
        yes_book = _make_book(best_bid=0.44, best_ask=0.47)
        no_book = _make_book(best_bid=0.44, best_ask=0.47)
        ob = {"yes": yes_book, "no": no_book}

        params = {"min_gap_pct": 3.0, "max_position_size": 100.0}
        signals = await strategy.evaluate(sample_market, ob, 100_000.0, params)

        assert len(signals) == 2
        sides = {s.side for s in signals}
        assert sides == {"YES", "NO"}
        # Both should be near the book ask
        for sig in signals:
            assert sig.target_price == pytest.approx(0.47, abs=0.001)

    async def test_no_gap(self, strategy, sample_market):
        """YES_ask=0.50 + NO_ask=0.51 = 1.01 > 0.97 → no combined mispricing signal."""
        yes_book = _make_book(best_bid=0.47, best_ask=0.50)
        no_book = _make_book(best_bid=0.48, best_ask=0.51)
        ob = {"yes": yes_book, "no": no_book}

        params = {"min_gap_pct": 3.0, "max_position_size": 100.0, "min_book_depth": 500.0}
        signals = await strategy.evaluate(sample_market, ob, 100_000.0, params)

        # Combined = 1.01 → no gap. Both books have depth 500 ≥ min_book_depth → no thin book either.
        assert signals == []

    async def test_no_books_returns_empty(self, strategy, sample_market):
        """Empty orderbook → no signals."""
        signals = await strategy.evaluate(sample_market, {}, 100_000.0, {})
        assert signals == []
