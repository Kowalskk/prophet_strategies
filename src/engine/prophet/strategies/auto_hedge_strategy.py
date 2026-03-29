"""
Auto-Hedge Strategy — detects overconfident markets and hedges the cheap side.

When one side of a binary market is suspiciously cheap (< 12c) while the other
is expensive (> 85c), there is a potential mispricing.  Placing a hedge order
on the cheap side costs little and pays $1 if the market resolves unexpectedly.

Default parameters
------------------
- ``expensive_side_min``        : 0.85  — other side must be at least this expensive
- ``cheap_side_max``            : 0.12  — cheap side must be at or below this price
- ``cheap_side_target``         : 0.08  — try to buy at this price or below
- ``size_usd``                  : 10.0  — USD per signal
- ``min_book_depth``            : 10.0  — minimum liquidity on cheap side
- ``min_market_hours_remaining``: 1.0   — skip markets resolving too soon
- ``exit_strategy``             : "hold_to_resolution"

Logic
-----
1. Get YES best_ask and NO best_ask from the order book.
2. Detect overconfident pattern:
   - If YES is expensive and NO is cheap → emit NO signal.
   - If NO is expensive and YES is cheap → emit YES signal.
3. Confidence = 1.0 − expensive_side_price (the more expensive the main side,
   the more surprised the market would be if it resolves the other way).
4. Skip markets resolving in < min_market_hours_remaining hours.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class AutoHedgeStrategy(StrategyBase):
    """Detects overconfident markets and places cheap hedge bets on the overlooked side."""

    name = "auto_hedge"
    description = (
        "Detects overconfident markets and places cheap hedge bets on the overlooked side"
    )
    default_params: dict[str, Any] = {
        "expensive_side_min": 0.85,       # other side must be this expensive
        "cheap_side_max": 0.12,           # cheap side must be at or below this
        "cheap_side_target": 0.08,        # try to buy at this price or below
        "size_usd": 10.0,
        "min_book_depth": 10.0,           # minimum liquidity on cheap side
        "min_market_hours_remaining": 1.0,
        "exit_strategy": "hold_to_resolution",
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Detect overconfident pricing and emit hedge signals on the cheap side."""
        p = self.validate_params(params)

        # ----------------------------------------------------------------
        # Hours remaining check
        # ----------------------------------------------------------------
        hours_remaining = _hours_remaining(market)
        if hours_remaining is not None and hours_remaining < p["min_market_hours_remaining"]:
            logger.debug(
                "auto_hedge: market_id=%d too close to resolution (%.2fh remaining)",
                market.id, hours_remaining,
            )
            return []

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")

        yes_ask = _get_best_ask(yes_book)
        no_ask = _get_best_ask(no_book)

        if yes_ask is None or no_ask is None:
            logger.debug(
                "auto_hedge: market_id=%d missing ask prices (yes=%s no=%s)",
                market.id, yes_ask, no_ask,
            )
            return []

        signals: list[TradeSignal] = []

        # ----------------------------------------------------------------
        # Pattern A: YES is expensive, NO is cheap → hedge with NO
        # ----------------------------------------------------------------
        if yes_ask > p["expensive_side_min"] and no_ask <= p["cheap_side_max"]:
            target_price = min(no_ask, p["cheap_side_target"])
            target_price = max(0.001, target_price)
            confidence = min(0.99, 1.0 - yes_ask)
            implied_edge = round(1.0 - yes_ask - no_ask, 4)
            meta = {
                "expensive_side": "YES",
                "expensive_price": yes_ask,
                "cheap_side": "NO",
                "cheap_price": no_ask,
                "target_price": target_price,
                "implied_edge": implied_edge,
                "hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
            }
            logger.info(
                "auto_hedge: OVERCONFIDENT YES market_id=%d YES@%.4f NO@%.4f → hedge NO@%.4f",
                market.id, yes_ask, no_ask, target_price,
            )
            signals.append(
                TradeSignal(
                    market_id=market.id,
                    side="NO",
                    target_price=round(target_price, 4),
                    size_usd=p["size_usd"],
                    confidence=confidence,
                    exit_strategy=p["exit_strategy"],
                    exit_params={},
                    metadata=meta,
                    strategy=self.name,
                )
            )

        # ----------------------------------------------------------------
        # Pattern B: NO is expensive, YES is cheap → hedge with YES
        # ----------------------------------------------------------------
        elif no_ask > p["expensive_side_min"] and yes_ask <= p["cheap_side_max"]:
            target_price = min(yes_ask, p["cheap_side_target"])
            target_price = max(0.001, target_price)
            confidence = min(0.99, 1.0 - no_ask)
            implied_edge = round(1.0 - yes_ask - no_ask, 4)
            meta = {
                "expensive_side": "NO",
                "expensive_price": no_ask,
                "cheap_side": "YES",
                "cheap_price": yes_ask,
                "target_price": target_price,
                "implied_edge": implied_edge,
                "hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
            }
            logger.info(
                "auto_hedge: OVERCONFIDENT NO market_id=%d NO@%.4f YES@%.4f → hedge YES@%.4f",
                market.id, no_ask, yes_ask, target_price,
            )
            signals.append(
                TradeSignal(
                    market_id=market.id,
                    side="YES",
                    target_price=round(target_price, 4),
                    size_usd=p["size_usd"],
                    confidence=confidence,
                    exit_strategy=p["exit_strategy"],
                    exit_params={},
                    metadata=meta,
                    strategy=self.name,
                )
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise auto_hedge parameters."""
        p = self._merge_params(params)

        p["expensive_side_min"] = float(p["expensive_side_min"])
        p["cheap_side_max"] = float(p["cheap_side_max"])
        p["cheap_side_target"] = float(p["cheap_side_target"])
        p["size_usd"] = float(p["size_usd"])
        p["min_book_depth"] = float(p["min_book_depth"])
        p["min_market_hours_remaining"] = float(p["min_market_hours_remaining"])
        p["exit_strategy"] = str(p["exit_strategy"])

        if not 0.0 < p["expensive_side_min"] <= 1.0:
            raise ValueError(
                f"expensive_side_min must be in (0, 1], got {p['expensive_side_min']}"
            )
        if not 0.0 < p["cheap_side_max"] < p["expensive_side_min"]:
            raise ValueError(
                f"cheap_side_max must be in (0, expensive_side_min), "
                f"got {p['cheap_side_max']}"
            )
        if not 0.0 < p["cheap_side_target"] <= p["cheap_side_max"]:
            raise ValueError(
                f"cheap_side_target must be in (0, cheap_side_max], "
                f"got {p['cheap_side_target']}"
            )
        if p["size_usd"] <= 0:
            raise ValueError(f"size_usd must be positive, got {p['size_usd']}")
        if p["min_book_depth"] < 0:
            raise ValueError(f"min_book_depth must be non-negative, got {p['min_book_depth']}")
        if p["min_market_hours_remaining"] < 0:
            raise ValueError(
                f"min_market_hours_remaining must be non-negative, "
                f"got {p['min_market_hours_remaining']}"
            )

        return p


class AutoHedgeAggressiveStrategy(AutoHedgeStrategy):
    """Aggressive auto-hedge: wider thresholds, larger size."""

    name = "auto_hedge_aggressive"
    description = (
        "Aggressive auto-hedge: triggers at expensive_side_min=0.80, cheap_side_max=0.15. "
        "Catches more overconfident markets with larger $15 positions."
    )
    default_params: dict[str, Any] = {
        "expensive_side_min": 0.80,
        "cheap_side_max": 0.15,
        "cheap_side_target": 0.10,
        "size_usd": 15.0,
        "min_book_depth": 10.0,
        "min_market_hours_remaining": 1.0,
        "exit_strategy": "hold_to_resolution",
    }


class AutoHedgeSniperStrategy(AutoHedgeStrategy):
    """Sniper auto-hedge: strict thresholds, small size, extreme overconfidence only."""

    name = "auto_hedge_sniper"
    description = (
        "Sniper auto-hedge: only triggers at extreme overconfidence (expensive_side_min=0.92, "
        "cheap_side_max=0.06). Small $5 positions targeting extreme mispricings."
    )
    default_params: dict[str, Any] = {
        "expensive_side_min": 0.92,
        "cheap_side_max": 0.06,
        "cheap_side_target": 0.04,
        "size_usd": 5.0,
        "min_book_depth": 10.0,
        "min_market_hours_remaining": 1.0,
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


def _hours_remaining(market: Any) -> float | None:
    """Return hours until market resolution, or None if unavailable."""
    resolution_date = getattr(market, "resolution_date", None)
    if resolution_date is None:
        return None
    try:
        resolution = datetime.fromisoformat(str(resolution_date)).replace(tzinfo=timezone.utc)
        return (resolution - datetime.now(timezone.utc)).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None
