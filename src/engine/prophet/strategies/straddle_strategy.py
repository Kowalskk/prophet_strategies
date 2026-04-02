"""
Straddle Strategy — enters both YES and NO when the combined ask price is cheap.

Unlike VS (which places limit orders below mid hoping for a discount), straddle
enters directly at the current ask price on both sides.  The thesis:

  - If combined_ask < threshold, at resolution one side pays $1.00 — guaranteed
    return if combined_entry < 1.0.
  - Before resolution, the side that moves first hits a 3x–5x target.
    The winning side more than pays for both entries.

Risk profile
------------
Best case:  one side hits target_hit (3×–5×), the other is still open or
            eventually resolves in our favour too.
Base case:  one side resolves YES ($1.00), other resolves NO ($0.00).
            Net = $1.00 - combined_entry > 0 if combined_entry < 1.0.
Worst case: both sides entered at 0.48 each (combined 0.96), one resolves $1,
            one $0 → net PnL = $1.00 - $0.96 = +$0.04 (tiny but positive).

Variants
--------
straddle_x3  : target 3× (200% gain), enter when combined_ask ≤ 0.80
straddle_x5  : target 5× (400% gain), enter when combined_ask ≤ 0.70
straddle_te  : time_exit 3 days before expiry + target 2×, combined ≤ 0.85
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class StraddleStrategy(StrategyBase):
    """Buys both YES and NO when combined ask is below threshold."""

    name = "straddle_x3"
    description = (
        "Enters both YES and NO at current ask when combined_ask ≤ 0.80. "
        "Targets 3× on whichever side moves first. At resolution, one side "
        "always pays $1.00 guaranteeing a positive return on the pair."
    )
    default_params: dict[str, Any] = {
        "combined_ask_max": 0.80,   # only enter when YES_ask + NO_ask ≤ this
        "capital_per_side": 10.0,   # USD per YES and per NO order
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 200.0,   # 3× = 200% gain
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self.validate_params(params)

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")
        if yes_book is None or no_book is None:
            return []

        yes_ask = getattr(yes_book, "best_ask", None)
        no_ask = getattr(no_book, "best_ask", None)

        if yes_ask is None:
            asks = getattr(yes_book, "asks", None)
            if asks:
                yes_ask = float(asks[0].price)
        if no_ask is None:
            asks = getattr(no_book, "asks", None)
            if asks:
                no_ask = float(asks[0].price)

        if yes_ask is None or no_ask is None:
            return []

        if not (0.001 < yes_ask < 1.0 and 0.001 < no_ask < 1.0):
            return []

        combined_ask = yes_ask + no_ask
        if combined_ask >= p["combined_ask_max"]:
            logger.debug(
                "%s: combined_ask=%.4f >= max=%.4f — skip market_id=%s",
                self.name, combined_ask, p["combined_ask_max"], market.id,
            )
            return []

        exit_params = {"target_pct": p["sell_target_pct"]}
        meta = {
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "combined_ask": round(combined_ask, 4),
            "guaranteed_return_pct": round((1.0 - combined_ask) / combined_ask * 100, 1),
        }

        logger.info(
            "%s: market_id=%d YES@%.4f NO@%.4f combined=%.4f (guaranteed +%.1f%%)",
            self.name, market.id, yes_ask, no_ask, combined_ask, meta["guaranteed_return_pct"],
        )

        return [
            TradeSignal(
                market_id=market.id,
                side="YES",
                target_price=round(yes_ask, 4),
                size_usd=p["capital_per_side"],
                confidence=0.75,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata=meta,
                strategy=self.name,
            ),
            TradeSignal(
                market_id=market.id,
                side="NO",
                target_price=round(no_ask, 4),
                size_usd=p["capital_per_side"],
                confidence=0.75,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata=meta,
                strategy=self.name,
            ),
        ]

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        p["combined_ask_max"] = float(p["combined_ask_max"])
        p["capital_per_side"] = float(p["capital_per_side"])
        p["sell_target_pct"] = float(p["sell_target_pct"])
        if not 0.0 < p["combined_ask_max"] < 2.0:
            raise ValueError(f"combined_ask_max must be in (0, 2), got {p['combined_ask_max']}")
        return p


class StraddleX5(StraddleStrategy):
    """Straddle targeting 5× — only enters very cheap combined (≤ 0.70)."""

    name = "straddle_x5"
    description = (
        "Enters both YES and NO at current ask when combined_ask ≤ 0.70. "
        "Targets 5× on the winning side. High guaranteed return at resolution."
    )
    default_params: dict[str, Any] = {
        "combined_ask_max": 0.70,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 400.0,   # 5× = 400% gain
    }


class StraddleTimeExit(StraddleStrategy):
    """Straddle with time-based exit — never holds to resolution.

    Exits 3 days before expiry at market price to avoid total loss.
    Target 2× — faster rotation, higher win rate.
    """

    name = "straddle_te"
    description = (
        "Straddle both sides when combined_ask ≤ 0.85. "
        "Exits 3 days before resolution at market price (time_exit) "
        "or at 2× target — avoids 100% loss on resolution."
    )
    default_params: dict[str, Any] = {
        "combined_ask_max": 0.85,
        "capital_per_side": 10.0,
        "exit_strategy": "time_exit",
        "sell_target_pct": 100.0,   # 2× = 100% gain
        "days_before_expiry": 3.0,
    }

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = super().validate_params(params)
        p["days_before_expiry"] = float(p.get("days_before_expiry", 3.0))
        return p


ALL_STRADDLE_CLASSES = [StraddleStrategy, StraddleX5, StraddleTimeExit]
