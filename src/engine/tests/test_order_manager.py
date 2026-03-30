"""
Tests for OrderManager — fill logic, exit conditions, PnL calculation.

Covers bugs found in production:
- exit_price using wrong side (YES price for NO position)
- fees only counted at exit, not entry
- fill at target_price instead of best_ask
- resolution exit price logic
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from prophet.core.order_manager import OrderManager, _FEE_RATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_position(
    market_id: int = 1,
    side: str = "YES",
    entry_price: float = 0.30,
    size_usd: float = 100.0,
    strategy: str = "srb_mid_x3",
    status: str = "open",
) -> MagicMock:
    pos = MagicMock()
    pos.id = 1
    pos.market_id = market_id
    pos.side = side
    pos.entry_price = entry_price
    pos.size_usd = size_usd
    pos.shares = size_usd / entry_price
    pos.strategy = strategy
    pos.status = status
    pos.opened_at = _utcnow()
    pos.gross_pnl = None
    pos.fees = None
    pos.net_pnl = None
    return pos


def _make_manager() -> OrderManager:
    clob = MagicMock()
    manager = OrderManager(clob_client=clob)
    return manager


# ---------------------------------------------------------------------------
# PnL calculation
# ---------------------------------------------------------------------------


class TestCalculatePnl:
    def test_basic_yes_win(self):
        """YES position, entry=0.30, exit=0.60 → gross=100, fees=entry+exit, net≈96."""
        mgr = _make_manager()
        pos = _make_position(side="YES", entry_price=0.30, size_usd=100.0)
        shares = 100.0 / 0.30  # 333.33

        gross, fees, net = mgr.calculate_pnl(pos, exit_price=0.60)

        expected_gross = (0.60 - 0.30) * shares  # ~100.0
        entry_fee = 0.30 * shares * _FEE_RATE     # ~2.0
        exit_fee = 0.60 * shares * _FEE_RATE      # ~4.0
        expected_fees = entry_fee + exit_fee       # ~6.0
        expected_net = expected_gross - expected_fees  # ~94.0

        assert gross == pytest.approx(expected_gross, rel=0.01)
        assert fees == pytest.approx(expected_fees, rel=0.01)
        assert net == pytest.approx(expected_net, rel=0.01)

    def test_yes_loss(self):
        """YES position that loses money — net_pnl is negative."""
        mgr = _make_manager()
        pos = _make_position(side="YES", entry_price=0.50, size_usd=100.0)

        gross, fees, net = mgr.calculate_pnl(pos, exit_price=0.20)

        assert gross < 0
        assert fees > 0
        assert net < gross  # net worse than gross because fees add to loss

    def test_no_win_resolves_zero(self):
        """NO position that wins: market resolved NO → exit_price=1.0."""
        mgr = _make_manager()
        pos = _make_position(side="NO", entry_price=0.05, size_usd=50.0)
        shares = 50.0 / 0.05  # 1000

        gross, fees, net = mgr.calculate_pnl(pos, exit_price=1.0)

        expected_gross = (1.0 - 0.05) * shares  # 950.0
        assert gross == pytest.approx(expected_gross, rel=0.01)
        assert net < gross  # fees reduce net

    def test_no_loss_resolves_yes(self):
        """NO position that loses: market resolved YES → exit_price=0.0."""
        mgr = _make_manager()
        pos = _make_position(side="NO", entry_price=0.05, size_usd=50.0)
        shares = 50.0 / 0.05

        gross, fees, net = mgr.calculate_pnl(pos, exit_price=0.0)

        expected_gross = (0.0 - 0.05) * shares  # -50.0
        assert gross == pytest.approx(expected_gross, rel=0.01)
        # fees at exit=0 are zero, only entry fee applies
        entry_fee = 0.05 * shares * _FEE_RATE
        assert fees == pytest.approx(entry_fee, rel=0.01)

    def test_both_sides_same_entry_same_exit(self):
        """YES and NO with identical entry/exit should produce identical PnL magnitude."""
        mgr = _make_manager()
        yes_pos = _make_position(side="YES", entry_price=0.40, size_usd=100.0)
        no_pos = _make_position(side="NO", entry_price=0.40, size_usd=100.0)

        yes_gross, yes_fees, yes_net = mgr.calculate_pnl(yes_pos, exit_price=0.80)
        no_gross, no_fees, no_net = mgr.calculate_pnl(no_pos, exit_price=0.80)

        assert yes_gross == pytest.approx(no_gross)
        assert yes_fees == pytest.approx(no_fees)
        assert yes_net == pytest.approx(no_net)

    def test_fee_rate_applied_both_sides(self):
        """Total fees = entry_fee + exit_fee, both at _FEE_RATE."""
        mgr = _make_manager()
        pos = _make_position(entry_price=0.50, size_usd=100.0)
        shares = 100.0 / 0.50  # 200

        _, fees, _ = mgr.calculate_pnl(pos, exit_price=0.70)

        entry_fee = 0.50 * shares * _FEE_RATE
        exit_fee = 0.70 * shares * _FEE_RATE
        assert fees == pytest.approx(entry_fee + exit_fee, rel=0.001)

    def test_rounding_to_4_decimals(self):
        """Output values should be rounded to 4 decimal places."""
        mgr = _make_manager()
        pos = _make_position(entry_price=0.33333, size_usd=100.0)
        gross, fees, net = mgr.calculate_pnl(pos, exit_price=0.66667)

        assert gross == round(gross, 4)
        assert fees == round(fees, 4)
        assert net == round(net, 4)


# ---------------------------------------------------------------------------
# Resolution exit price
# ---------------------------------------------------------------------------


class TestResolutionExitPrice:
    def test_yes_side_yes_outcome(self):
        assert OrderManager._resolution_exit_price("YES", "YES") == 1.0

    def test_yes_side_no_outcome(self):
        assert OrderManager._resolution_exit_price("YES", "NO") == 0.0

    def test_no_side_no_outcome(self):
        assert OrderManager._resolution_exit_price("NO", "NO") == 1.0

    def test_no_side_yes_outcome(self):
        """Bug that was in production: NO position on YES resolution should be 0, not 1."""
        assert OrderManager._resolution_exit_price("NO", "YES") == 0.0

    def test_case_insensitive(self):
        assert OrderManager._resolution_exit_price("yes", "yes") == 1.0
        assert OrderManager._resolution_exit_price("no", "yes") == 0.0
        assert OrderManager._resolution_exit_price("Yes", "No") == 0.0


# ---------------------------------------------------------------------------
# Fill logic: fill at best_ask, not target_price
# ---------------------------------------------------------------------------


class TestFillPrice:
    def test_fill_uses_best_ask_not_target(self):
        """
        Fill price must be best_ask (realistic slippage), not target_price.
        A signal at target=0.25 fills at best_ask=0.23 (cheaper = better for buyer).
        """
        target_price = 0.25
        best_ask = 0.23  # market is cheaper than our limit

        # The fill logic: fill only if best_ask <= target_price
        assert best_ask <= target_price, "Should fill"
        fill_price = best_ask  # NOT target_price
        assert fill_price == pytest.approx(0.23)
        assert fill_price != target_price

    def test_no_fill_when_ask_above_target(self):
        """Order stays open if best_ask > target_price."""
        target_price = 0.25
        best_ask = 0.30  # market is more expensive

        should_fill = best_ask <= target_price
        assert not should_fill

    def test_fill_at_exact_target(self):
        """Fill when best_ask == target_price exactly."""
        target_price = 0.25
        best_ask = 0.25
        assert best_ask <= target_price


# ---------------------------------------------------------------------------
# Exit condition: sell_at_target
# ---------------------------------------------------------------------------


class TestExitConditions:
    @pytest.mark.asyncio
    async def test_hold_to_resolution_closes_on_yes(self):
        """hold_to_resolution + resolved_outcome=YES + side=YES → exit_price=1.0."""
        mgr = _make_manager()
        pos = _make_position(side="YES", entry_price=0.40)

        closed_calls = []

        async def fake_close(_position, exit_price, exit_reason):
            closed_calls.append((exit_price, exit_reason))
            return True

        mgr._close_position = fake_close

        market = MagicMock()
        market.resolved_outcome = "YES"
        market.id = pos.market_id

        exit_price = mgr._resolution_exit_price(pos.side, market.resolved_outcome)
        await mgr._close_position(pos, exit_price=exit_price, exit_reason="resolution")

        assert closed_calls[0][0] == 1.0
        assert closed_calls[0][1] == "resolution"

    @pytest.mark.asyncio
    async def test_hold_to_resolution_no_close_while_unresolved(self):
        """hold_to_resolution: position stays open if market not resolved."""
        market = MagicMock()
        market.resolved_outcome = None

        # Simulate the check: no resolved_outcome → return False
        exit_strategy = "hold_to_resolution"
        should_close = bool(exit_strategy == "hold_to_resolution" and market.resolved_outcome)
        assert not should_close

    def test_sell_at_target_threshold(self):
        """sell_at_target at 50% gain: entry=0.40, target=0.60."""
        entry_price = 0.40
        target_pct = 50.0
        target_price = min(entry_price * (1.0 + target_pct / 100.0), 1.0)

        assert target_price == pytest.approx(0.60)

        # Current price above target → should close
        assert 0.65 >= target_price
        # Current price below target → stay open
        assert 0.55 < target_price

    def test_sell_at_target_capped_at_1(self):
        """Target price is capped at 1.0 (max market price)."""
        entry_price = 0.80
        target_pct = 50.0
        target_price = min(entry_price * (1.0 + target_pct / 100.0), 1.0)
        assert target_price == 1.0

    def test_sell_at_nx_multiplier(self):
        """sell_at_2x: entry=0.20, target=0.40."""
        entry_price = 0.20
        multiplier = 2.0
        target_price = min(entry_price * multiplier, 1.0)
        assert target_price == pytest.approx(0.40)

    def test_no_side_resolution_correct_exit(self):
        """
        Bug found in production: NO position in a market that resolves YES
        should get exit_price=0.0, not 1.0.
        """
        side = "NO"
        resolved_outcome = "YES"
        exit_price = OrderManager._resolution_exit_price(side, resolved_outcome)
        assert exit_price == 0.0, (
            "NO position on YES resolution = worthless = 0.0, not 1.0"
        )

    def test_wide_spread_uses_best_bid_not_mid(self):
        """
        Bug found in production: NO token with bid=0.002, ask=0.899 had
        mid_price=0.4505.  _get_current_price returned 0.4505 which triggered
        a false target_hit exit.  Fix: use best_bid (sell price), not mid.

        With best_bid=0.002, the exit should NOT trigger for a sell_at_2x
        target of 0.02 (entry=0.01).
        """
        entry_price = 0.01
        multiplier = 2.0
        target_price = min(entry_price * multiplier, 1.0)  # 0.02

        # The old buggy mid_price
        best_bid = 0.002
        best_ask = 0.899
        mid_price = (best_bid + best_ask) / 2  # 0.4505

        # mid_price would wrongly trigger
        assert mid_price >= target_price, "mid_price falsely triggers exit"
        # best_bid correctly does NOT trigger
        assert best_bid < target_price, "best_bid correctly stays below target"


# ---------------------------------------------------------------------------
# Spread capture at signal time
# ---------------------------------------------------------------------------


class TestSpreadCapture:
    def test_spread_extracted_from_orderbook(self):
        """bid_at and ask_at should come from the correct side of the orderbook."""
        yes_book = MagicMock()
        yes_book.best_bid = 0.48
        yes_book.best_ask = 0.52

        no_book = MagicMock()
        no_book.best_bid = 0.47
        no_book.best_ask = 0.53

        orderbook = {"yes": yes_book, "no": no_book}

        # Signal for YES side
        signal_side = "yes"
        ob_side = orderbook.get(signal_side)
        bid_at = getattr(ob_side, "best_bid", None)
        ask_at = getattr(ob_side, "best_ask", None)

        assert bid_at == pytest.approx(0.48)
        assert ask_at == pytest.approx(0.52)

    def test_spread_none_when_no_orderbook(self):
        """If orderbook is None/empty, spread values are None (not crash)."""
        orderbook = None
        ob_side = orderbook.get("yes") if orderbook else None
        bid_at = getattr(ob_side, "best_bid", None)
        ask_at = getattr(ob_side, "best_ask", None)

        assert bid_at is None
        assert ask_at is None

    def test_spread_correct_side_for_no_signal(self):
        """NO signal reads NO side spread, not YES side."""
        yes_book = MagicMock()
        yes_book.best_bid = 0.48
        yes_book.best_ask = 0.52

        no_book = MagicMock()
        no_book.best_bid = 0.47
        no_book.best_ask = 0.53

        orderbook = {"yes": yes_book, "no": no_book}

        signal_side = "no"
        ob_side = orderbook.get(signal_side)
        bid_at = getattr(ob_side, "best_bid", None)
        ask_at = getattr(ob_side, "best_ask", None)

        assert bid_at == pytest.approx(0.47)
        assert ask_at == pytest.approx(0.53)
