"""
Ladder Market Maker Strategy — multi-rung limit orders on both sides.

Places orders at MULTIPLE price rungs simultaneously on both YES and NO sides.
When YES+NO combined cost < merge_threshold, buys both sides for a guaranteed
$1 payout at resolution.

Default parameters
------------------
- ``min_price``        : 0.03  — lowest rung price
- ``max_price``        : 0.20  — highest rung price
- ``num_rungs``        : 5     — number of price levels
- ``capital_per_rung`` : 5.0   — USD per rung
- ``rung_spacing``     : "geometric" — "linear" or "geometric"
- ``merge_threshold``  : 0.95  — emit merge signal when YES+NO < this
- ``min_book_depth``   : 20.0  — minimum depth required
- ``exit_strategy``    : "hold_to_resolution"

Logic
-----
1. Get YES best_ask and NO best_ask from orderbook.
2. Merge check first: if yes_ask + no_ask < merge_threshold, emit both-side
   signals at the current ask prices with doubled capital.
3. Ladder check: generate price rungs between min_price and max_price.
   For each rung and each side, emit a signal if best_ask > rung_price.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class LadderMMStrategy(StrategyBase):
    """Multi-rung limit orders on both sides. Profits from spread capture and YES+NO merge."""

    name = "ladder_mm"
    description = (
        "Multi-rung limit orders on both sides. Profits from spread capture and "
        "YES+NO merge opportunities"
    )
    default_params: dict[str, Any] = {
        "min_price": 0.03,            # lowest rung
        "max_price": 0.20,            # highest rung
        "num_rungs": 5,               # number of price levels
        "capital_per_rung": 5.0,      # USD per rung
        "rung_spacing": "geometric",  # "linear" or "geometric"
        "merge_threshold": 0.95,      # emit merge signal when YES+NO combined < this
        "min_book_depth": 20.0,       # minimum depth required
        "exit_strategy": "hold_to_resolution",
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Evaluate market, check for merge opportunity, then place ladder rungs."""
        p = self.validate_params(params)

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")

        yes_ask = _get_best_ask(yes_book)
        no_ask = _get_best_ask(no_book)

        signals: list[TradeSignal] = []

        # ----------------------------------------------------------------
        # Case 1: Merge opportunity — YES + NO combined < merge_threshold
        # ----------------------------------------------------------------
        if yes_ask is not None and no_ask is not None:
            combined = yes_ask + no_ask
            if combined < p["merge_threshold"]:
                confidence = min(0.95, (p["merge_threshold"] - combined) / p["merge_threshold"])
                merge_size = p["capital_per_rung"] * 2.0
                meta = {
                    "signal_type": "merge_opportunity",
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "combined_cost": combined,
                    "merge_threshold": p["merge_threshold"],
                    "edge": round(1.0 - combined, 4),
                }
                logger.info(
                    "ladder_mm: MERGE OPPORTUNITY market_id=%d "
                    "YES@%.4f + NO@%.4f = %.4f (threshold=%.4f)",
                    market.id, yes_ask, no_ask, combined, p["merge_threshold"],
                )
                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side="YES",
                        target_price=round(yes_ask, 4),
                        size_usd=merge_size,
                        confidence=confidence,
                        exit_strategy="hold_to_resolution",
                        exit_params={},
                        metadata=meta,
                        strategy=self.name,
                    )
                )
                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side="NO",
                        target_price=round(no_ask, 4),
                        size_usd=merge_size,
                        confidence=confidence,
                        exit_strategy="hold_to_resolution",
                        exit_params={},
                        metadata=meta,
                        strategy=self.name,
                    )
                )
                # Merge is the priority signal — return immediately
                return signals

        # ----------------------------------------------------------------
        # Case 2: Ladder rungs — place orders at each rung price level
        # ----------------------------------------------------------------
        rungs = _generate_rungs(p["min_price"], p["max_price"], p["num_rungs"], p["rung_spacing"])

        for rung_idx, rung_price in enumerate(rungs):
            for side, ask in [("YES", yes_ask), ("NO", no_ask)]:
                if ask is None:
                    continue
                # Only place order if best_ask is above the rung (i.e. we can get a better price)
                if ask <= rung_price:
                    continue
                if rung_price > p["max_price"] or rung_price < p["min_price"]:
                    continue

                meta = {
                    "signal_type": "ladder_rung",
                    "rung_index": rung_idx,
                    "rung_price": round(rung_price, 4),
                    "side": side,
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "rung_spacing": p["rung_spacing"],
                }
                logger.debug(
                    "ladder_mm: rung %d market_id=%d side=%s rung_price=%.4f ask=%.4f",
                    rung_idx, market.id, side, rung_price, ask,
                )
                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side=side,
                        target_price=round(rung_price, 4),
                        size_usd=p["capital_per_rung"],
                        confidence=0.5,
                        exit_strategy="hold_to_resolution",
                        exit_params={},
                        metadata=meta,
                        strategy=self.name,
                    )
                )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise ladder_mm parameters."""
        p = self._merge_params(params)

        p["min_price"] = float(p["min_price"])
        p["max_price"] = float(p["max_price"])
        p["num_rungs"] = int(p["num_rungs"])
        p["capital_per_rung"] = float(p["capital_per_rung"])
        p["rung_spacing"] = str(p["rung_spacing"]).lower()
        p["merge_threshold"] = float(p["merge_threshold"])
        p["min_book_depth"] = float(p["min_book_depth"])
        p["exit_strategy"] = str(p["exit_strategy"])

        if not 0.0 < p["min_price"] < p["max_price"] <= 1.0:
            raise ValueError(
                f"must have 0 < min_price < max_price <= 1, "
                f"got min_price={p['min_price']}, max_price={p['max_price']}"
            )
        if p["num_rungs"] < 1:
            raise ValueError(f"num_rungs must be >= 1, got {p['num_rungs']}")
        if p["capital_per_rung"] <= 0:
            raise ValueError(f"capital_per_rung must be positive, got {p['capital_per_rung']}")
        if p["rung_spacing"] not in ("linear", "geometric"):
            raise ValueError(
                f"rung_spacing must be 'linear' or 'geometric', got {p['rung_spacing']!r}"
            )
        if not 0.0 < p["merge_threshold"] <= 1.0:
            raise ValueError(
                f"merge_threshold must be in (0, 1], got {p['merge_threshold']}"
            )
        if p["min_book_depth"] < 0:
            raise ValueError(f"min_book_depth must be non-negative, got {p['min_book_depth']}")

        return p


class LadderMMWideStrategy(LadderMMStrategy):
    """Wide ladder: low prices, many rungs, broad spread capture."""

    name = "ladder_mm_wide"
    description = (
        "Wide ladder market maker: min=0.02, max=0.30, 8 rungs. "
        "Captures broad spread opportunities across the full price range."
    )
    default_params: dict[str, Any] = {
        "min_price": 0.02,
        "max_price": 0.30,
        "num_rungs": 8,
        "capital_per_rung": 3.0,
        "rung_spacing": "geometric",
        "merge_threshold": 0.95,
        "min_book_depth": 20.0,
        "exit_strategy": "hold_to_resolution",
    }


class LadderMMTightStrategy(LadderMMStrategy):
    """Tight ladder: higher prices, fewer rungs, stronger merge threshold."""

    name = "ladder_mm_tight"
    description = (
        "Tight ladder market maker: min=0.08, max=0.25, 4 rungs, merge_threshold=0.92. "
        "Higher capital per rung, targets tighter merge spreads."
    )
    default_params: dict[str, Any] = {
        "min_price": 0.08,
        "max_price": 0.25,
        "num_rungs": 4,
        "capital_per_rung": 10.0,
        "rung_spacing": "geometric",
        "merge_threshold": 0.92,
        "min_book_depth": 20.0,
        "exit_strategy": "hold_to_resolution",
    }


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


def _generate_rungs(
    min_price: float,
    max_price: float,
    num_rungs: int,
    spacing: str,
) -> list[float]:
    """Generate rung price levels between min_price and max_price."""
    if num_rungs == 1:
        return [(min_price + max_price) / 2.0]

    if spacing == "geometric":
        # logspace between log(min_price) and log(max_price)
        log_min = math.log(min_price)
        log_max = math.log(max_price)
        step = (log_max - log_min) / (num_rungs - 1)
        return [math.exp(log_min + i * step) for i in range(num_rungs)]
    else:
        # linear spacing
        step = (max_price - min_price) / (num_rungs - 1)
        return [min_price + i * step for i in range(num_rungs)]
