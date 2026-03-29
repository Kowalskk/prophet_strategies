"""
Liquidity Sniper Strategy — exploits YES+NO mispricing and liquidity gaps.

Binary markets must satisfy YES_price + NO_price ≈ 1.0.  When liquidity is
thin or market makers are slow, the combined cost can fall below 1.0,
creating a risk-free (or near risk-free) arbitrage opportunity.

Default parameters
------------------
- ``min_gap_pct``        : 3.0 — minimum gap below 1.0 to trigger (%)
- ``max_position_size``  : 100.0 — max USD per signal
- ``exit_timeout_hours`` : 24 — close if still open after this many hours
- ``min_book_depth``     : 50.0 — minimum USD depth required to act

Logic
-----
1. Fetch best_ask for YES and NO.
2. Combined cost = best_ask_yes + best_ask_no.
3. If combined < (1 - min_gap_pct / 100): both sides are cheap → buy BOTH.
4. Additionally, if one side has thin depth (< min_book_depth): place an
   order at the gap price on the thin side only.
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class LiquiditySniperStrategy(StrategyBase):
    """Exploits liquidity gaps and YES+NO combined cost below 100¢."""

    name = "liquidity_sniper"
    description = (
        "Detects when YES + NO combined cost < 97¢ and buys both sides for "
        "near-guaranteed profit. Also targets single-side gaps when depth is thin."
    )
    default_params: dict[str, Any] = {
        "min_gap_pct": 3.0,           # combined must be < (1 - 0.03) = 0.97
        "max_position_size": 100.0,   # USD per signal
        "exit_timeout_hours": 168,    # 7 days — wait for market resolution
        "min_book_depth": 50.0,       # minimum USD depth on each side to act
        "enable_thin_book": False,    # disable speculative thin-book signals
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Detect YES+NO mispricing and/or thin-book opportunities."""
        p = self.validate_params(params)

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")

        yes_ask = _get_best_ask(yes_book)
        no_ask = _get_best_ask(no_book)

        yes_depth = _get_ask_depth(yes_book)
        no_depth = _get_ask_depth(no_book)

        signals: list[TradeSignal] = []
        threshold = 1.0 - p["min_gap_pct"] / 100.0

        # ----------------------------------------------------------------
        # Case 1: Both sides are priced below threshold (combined < 97¢)
        # ----------------------------------------------------------------
        if yes_ask is not None and no_ask is not None:
            combined = yes_ask + no_ask
            if combined < threshold:
                gap_pct = round((1.0 - combined) * 100.0, 2)
                logger.info(
                    "liquidity_sniper: COMBINED MISPRICING market_id=%d "
                    "YES@%.4f + NO@%.4f = %.4f (gap=%.2f%%)",
                    market.id, yes_ask, no_ask, combined, gap_pct,
                )

                size_per_side = min(p["max_position_size"] / 2.0, p["max_position_size"])
                meta = {
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "combined_cost": combined,
                    "gap_pct": gap_pct,
                    "signal_type": "combined_mispricing",
                }
                exit_params = {"timeout_hours": p["exit_timeout_hours"]}

                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side="YES",
                        target_price=round(yes_ask, 4),
                        size_usd=size_per_side,
                        confidence=min(0.99, gap_pct / 10.0),
                        exit_strategy="sell_at_target",
                        exit_params=dict(exit_params, target_pct=50.0),
                        metadata=meta,
                        strategy=self.name,
                    )
                )
                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side="NO",
                        target_price=round(no_ask, 4),
                        size_usd=size_per_side,
                        confidence=min(0.99, gap_pct / 10.0),
                        exit_strategy="sell_at_target",
                        exit_params=dict(exit_params, target_pct=50.0),
                        metadata=meta,
                        strategy=self.name,
                    )
                )
                return signals  # Combined mispricing is the primary signal; return early

        # ----------------------------------------------------------------
        # Case 2: Single-side thin book — gap price on the thin side
        # ----------------------------------------------------------------
        if not p.get("enable_thin_book", False):
            return signals

        for side, ask, depth, other_ask in [
            ("YES", yes_ask, yes_depth, no_ask),
            ("NO", no_ask, no_depth, yes_ask),
        ]:
            if ask is None or other_ask is None:
                continue
            if depth is not None and depth < p["min_book_depth"]:
                # The thin side has insufficient liquidity — there may be
                # a gap price to exploit
                implied_fair = 1.0 - other_ask
                if ask > implied_fair * (1.0 + p["min_gap_pct"] / 100.0):
                    # The thin side is overpriced relative to the other side
                    continue

                gap_target = max(ask * 0.95, 0.001)  # place slightly below current ask
                logger.info(
                    "liquidity_sniper: thin book on %s side, market_id=%d "
                    "depth=%.2f ask=%.4f gap_target=%.4f",
                    side, market.id, depth, ask, gap_target,
                )
                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side=side,
                        target_price=round(gap_target, 4),
                        size_usd=min(p["max_position_size"], 50.0),
                        confidence=0.6,
                        exit_strategy="sell_at_target",
                        exit_params={
                            "target_pct": 30.0,
                            "timeout_hours": p["exit_timeout_hours"],
                        },
                        metadata={
                            "signal_type": "thin_book",
                            "side": side,
                            "book_depth": depth,
                            "best_ask": ask,
                            "implied_fair": implied_fair,
                        },
                        strategy=self.name,
                    )
                )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise liquidity_sniper parameters."""
        p = self._merge_params(params)

        p["min_gap_pct"] = float(p["min_gap_pct"])
        p["max_position_size"] = float(p["max_position_size"])
        p["exit_timeout_hours"] = int(p["exit_timeout_hours"])
        p["min_book_depth"] = float(p["min_book_depth"])
        p["enable_thin_book"] = bool(p.get("enable_thin_book", False))

        if p["min_gap_pct"] <= 0:
            raise ValueError(f"min_gap_pct must be positive, got {p['min_gap_pct']}")
        if p["max_position_size"] <= 0:
            raise ValueError(f"max_position_size must be positive, got {p['max_position_size']}")
        if p["exit_timeout_hours"] <= 0:
            raise ValueError(f"exit_timeout_hours must be positive, got {p['exit_timeout_hours']}")
        if p["min_book_depth"] < 0:
            raise ValueError(f"min_book_depth must be non-negative, got {p['min_book_depth']}")

        return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_best_ask(book: Any | None) -> float | None:
    """Return the best ask price from an OrderBook, or None."""
    if book is None:
        return None
    best_ask = getattr(book, "best_ask", None)
    if best_ask is not None:
        return float(best_ask)
    asks = getattr(book, "asks", None)
    if asks:
        return float(asks[0].price)
    return None


def _get_ask_depth(book: Any | None) -> float | None:
    """Return the ask_depth_10pct from an OrderBook, or None."""
    if book is None:
        return None
    return getattr(book, "ask_depth_10pct", None)
