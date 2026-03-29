"""
Pre-Window Sniper Strategy — queue-priority orders in the 18–30h pre-resolution window.

Markets open 24h before resolution at ~50/50 prices and then drift toward the
true outcome price.  Placing limit orders early at or below the 50c level
secures FIFO queue priority before the drift kicks in.

Default parameters
------------------
- ``target_price``         : 0.50 — enter at or below the 50/50 price
- ``min_price``            : 0.35 — skip if price has already drifted below this
- ``size_usd``             : 15.0
- ``side``                 : "BOTH" — YES, NO, or BOTH
- ``min_hours_remaining``  : 18.0 — only enter if > 18h remaining (pre-window)
- ``max_hours_remaining``  : 30.0 — don't enter if > 30h remaining (too early)
- ``exit_strategy``        : "sell_at_target"
- ``sell_target_pct``      : 60.0 — sell at 60% gain from entry

Logic
-----
1. Compute hours_remaining from market.resolution_date.
2. Skip if outside the [min_hours_remaining, max_hours_remaining] window.
3. For each side in ``side`` param (YES, NO, or both):
   - Get best_ask.
   - Skip if best_ask > target_price (market hasn't settled yet).
   - Skip if best_ask < min_price (opportunity already drifted past).
   - Emit signal with confidence proportional to distance below target_price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class PreWindowStrategy(StrategyBase):
    """Places early limit orders at fair value when market opens 24h pre-resolution."""

    name = "pre_window"
    description = (
        "Places early limit orders at fair value when market opens 24h pre-resolution "
        "to get FIFO queue priority"
    )
    default_params: dict[str, Any] = {
        "target_price": 0.50,         # enter at or below 50/50 price
        "min_price": 0.35,            # don't enter if price already drifted below this
        "size_usd": 15.0,
        "side": "BOTH",               # YES, NO, or BOTH
        "min_hours_remaining": 18.0,  # only enter if > 18h remaining (pre-window)
        "max_hours_remaining": 30.0,  # don't enter if > 30h remaining (too early)
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 60.0,      # sell at 60% gain from entry
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Check if market is in pre-window zone and emit queue-priority signals."""
        p = self.validate_params(params)

        # ----------------------------------------------------------------
        # Hours remaining check — must be inside the pre-window zone
        # ----------------------------------------------------------------
        hours_remaining = _hours_remaining(market)
        if hours_remaining is None:
            logger.debug(
                "pre_window: market_id=%d has no resolution_date — skipping",
                market.id,
            )
            return []

        if hours_remaining < p["min_hours_remaining"]:
            logger.debug(
                "pre_window: market_id=%d too late (%.2fh < min %.2fh)",
                market.id, hours_remaining, p["min_hours_remaining"],
            )
            return []

        if hours_remaining > p["max_hours_remaining"]:
            logger.debug(
                "pre_window: market_id=%d too early (%.2fh > max %.2fh)",
                market.id, hours_remaining, p["max_hours_remaining"],
            )
            return []

        # We are in the pre-window zone
        window_description = (
            f"{hours_remaining:.1f}h remaining "
            f"(window: {p['min_hours_remaining']}–{p['max_hours_remaining']}h)"
        )

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")

        # Determine which sides to evaluate
        side_param = p["side"].upper()
        if side_param == "BOTH":
            sides_to_check = [("YES", yes_book), ("NO", no_book)]
        elif side_param == "YES":
            sides_to_check = [("YES", yes_book)]
        elif side_param == "NO":
            sides_to_check = [("NO", no_book)]
        else:
            logger.warning(
                "pre_window: unrecognised side=%r for market_id=%d — defaulting to BOTH",
                side_param, market.id,
            )
            sides_to_check = [("YES", yes_book), ("NO", no_book)]

        signals: list[TradeSignal] = []

        for side, book in sides_to_check:
            best_ask = _get_best_ask(book)
            if best_ask is None:
                logger.debug(
                    "pre_window: market_id=%d side=%s has no best_ask — skipping",
                    market.id, side,
                )
                continue

            # Skip if price hasn't come down to target yet
            if best_ask > p["target_price"]:
                logger.debug(
                    "pre_window: market_id=%d side=%s ask=%.4f > target=%.4f — not ready",
                    market.id, side, best_ask, p["target_price"],
                )
                continue

            # Skip if price has already drifted too far (opportunity missed)
            if best_ask < p["min_price"]:
                logger.debug(
                    "pre_window: market_id=%d side=%s ask=%.4f < min_price=%.4f — drifted past",
                    market.id, side, best_ask, p["min_price"],
                )
                continue

            # Confidence: how far below target_price is the current ask?
            confidence = min(
                0.75,
                (p["target_price"] - best_ask + 0.01) / p["target_price"],
            )

            meta = {
                "hours_remaining": round(hours_remaining, 2),
                "best_ask": best_ask,
                "target_price": p["target_price"],
                "min_price": p["min_price"],
                "side": side,
                "window_description": window_description,
            }

            logger.info(
                "pre_window: PRE-WINDOW SIGNAL market_id=%d side=%s ask=%.4f "
                "confidence=%.3f %s",
                market.id, side, best_ask, confidence, window_description,
            )

            signals.append(
                TradeSignal(
                    market_id=market.id,
                    side=side,
                    target_price=round(best_ask, 4),
                    size_usd=p["size_usd"],
                    confidence=confidence,
                    exit_strategy=p["exit_strategy"],
                    exit_params={"target_pct": p["sell_target_pct"]},
                    metadata=meta,
                    strategy=self.name,
                )
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise pre_window parameters."""
        p = self._merge_params(params)

        p["target_price"] = float(p["target_price"])
        p["min_price"] = float(p["min_price"])
        p["size_usd"] = float(p["size_usd"])
        p["side"] = str(p["side"]).upper()
        p["min_hours_remaining"] = float(p["min_hours_remaining"])
        p["max_hours_remaining"] = float(p["max_hours_remaining"])
        p["exit_strategy"] = str(p["exit_strategy"])
        p["sell_target_pct"] = float(p["sell_target_pct"])

        if not 0.0 < p["min_price"] < p["target_price"] <= 1.0:
            raise ValueError(
                f"must have 0 < min_price < target_price <= 1, "
                f"got min_price={p['min_price']}, target_price={p['target_price']}"
            )
        if p["size_usd"] <= 0:
            raise ValueError(f"size_usd must be positive, got {p['size_usd']}")
        if p["side"] not in ("YES", "NO", "BOTH"):
            raise ValueError(
                f"side must be 'YES', 'NO', or 'BOTH', got {p['side']!r}"
            )
        if p["min_hours_remaining"] < 0:
            raise ValueError(
                f"min_hours_remaining must be non-negative, got {p['min_hours_remaining']}"
            )
        if p["max_hours_remaining"] <= p["min_hours_remaining"]:
            raise ValueError(
                f"max_hours_remaining must be > min_hours_remaining, "
                f"got max={p['max_hours_remaining']}, min={p['min_hours_remaining']}"
            )
        if p["sell_target_pct"] <= 0:
            raise ValueError(f"sell_target_pct must be positive, got {p['sell_target_pct']}")

        return p


class PreWindowEarlyStrategy(PreWindowStrategy):
    """Early pre-window: targets the 24–36h zone, slightly above 50c entry."""

    name = "pre_window_early"
    description = (
        "Early pre-window sniper: enters in the 24–36h zone at target_price=0.52. "
        "Aims for 80% gain exit as price drifts post-open."
    )
    default_params: dict[str, Any] = {
        "target_price": 0.52,
        "min_price": 0.35,
        "size_usd": 15.0,
        "side": "BOTH",
        "min_hours_remaining": 24.0,
        "max_hours_remaining": 36.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 80.0,
    }


class PreWindowLateStrategy(PreWindowStrategy):
    """Late pre-window: targets the 12–20h zone, lower entry price."""

    name = "pre_window_late"
    description = (
        "Late pre-window sniper: enters in the 12–20h zone at target_price=0.45. "
        "Targets 40% gain exit as market is closer to resolution drift."
    )
    default_params: dict[str, Any] = {
        "target_price": 0.45,
        "min_price": 0.30,
        "size_usd": 15.0,
        "side": "BOTH",
        "min_hours_remaining": 12.0,
        "max_hours_remaining": 20.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 40.0,
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
