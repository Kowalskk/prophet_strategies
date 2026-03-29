"""
Reversal Strategy — mean-reversion cycling: buys dips, sells bounces, re-enters on next dip.

Best suited for markets that oscillate rather than trend to a definitive resolution.
The strategy buys when the price falls to or below ``entry_price``, targets a exit at
``take_profit_price``, and bails out if the price drops to ``stop_loss_price``.

Default parameters
------------------
- ``entry_price``               : 0.45  — buy when best_ask <= this
- ``take_profit_price``         : 0.60  — target exit price
- ``stop_loss_price``           : 0.30  — bail if price falls here
- ``size_usd``                  : 20.0  — USD per order
- ``max_cycles``                : 5     — max buy/sell rounds (informational)
- ``side``                      : "YES" — "YES", "NO", or "BOTH"
- ``min_price_distance``        : 0.02  — skip if (take_profit - best_ask) < this
- ``min_market_hours_remaining``: 2.0   — skip markets resolving soon
- ``exit_strategy``             : "sell_at_target"
- ``sell_target_pct``           : 33.0  — ~33% gain from entry

Logic
-----
1. Check market has at least ``min_market_hours_remaining`` hours until resolution.
2. For each side in the ``side`` param ("YES", "NO", or both):
   a. Read best_ask from the order book.
   b. Skip if best_ask > entry_price (not cheap enough).
   c. Skip if best_ask <= stop_loss_price (already fallen too far).
   d. Skip if take_profit_price <= best_ask (no room to profit).
   e. Skip if (take_profit_price - best_ask) < min_price_distance (spread too tight).
   f. confidence = min((entry_price - best_ask) / entry_price, 0.9)
   g. Emit TradeSignal with exit_strategy="sell_at_target".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class ReversalStrategy(StrategyBase):
    """Mean-reversion cycling strategy: buys dips, sells bounces, re-enters on next dip."""

    name = "reversal"
    description = (
        "Mean-reversion cycling strategy: buys dips, sells bounces, re-enters on next dip"
    )
    default_params: dict[str, Any] = {
        "entry_price": 0.45,                 # buy when price at or below this
        "take_profit_price": 0.60,           # sell target price
        "stop_loss_price": 0.30,             # bail if price falls here
        "size_usd": 20.0,
        "max_cycles": 5,                     # max buy/sell rounds (informational)
        "side": "YES",                       # YES, NO, or BOTH
        "min_price_distance": 0.02,          # slippage protection
        "min_market_hours_remaining": 2.0,   # skip markets resolving soon
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 33.0,             # ~33% gain from entry
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Evaluate market and return reversal signals if conditions are met."""
        p = self.validate_params(params)

        # --- hours remaining check -------------------------------------------
        hours_remaining: float | None = None
        if market.resolution_date:
            try:
                resolution_dt = datetime.fromisoformat(
                    str(market.resolution_date)
                ).replace(tzinfo=timezone.utc)
                hours_remaining = (
                    resolution_dt - datetime.now(timezone.utc)
                ).total_seconds() / 3600.0
            except (ValueError, TypeError):
                hours_remaining = None

        if hours_remaining is not None and hours_remaining < p["min_market_hours_remaining"]:
            logger.debug(
                "reversal: market_id=%s resolves in %.1fh < %.1fh min — skipping",
                market.id, hours_remaining, p["min_market_hours_remaining"],
            )
            return []

        # --- determine which sides to evaluate --------------------------------
        side_param: str = str(p["side"]).upper()
        sides_to_check: list[str]
        if side_param == "BOTH":
            sides_to_check = ["YES", "NO"]
        elif side_param in ("YES", "NO"):
            sides_to_check = [side_param]
        else:
            logger.warning(
                "reversal: unknown side=%r for market_id=%s — skipping",
                p["side"], market.id,
            )
            return []

        signals: list[TradeSignal] = []

        for side in sides_to_check:
            book = orderbook.get(side.lower())
            if book is None:
                logger.debug(
                    "reversal: no %s order book for market_id=%s", side, market.id
                )
                continue

            best_ask = getattr(book, "best_ask", None)
            if best_ask is None:
                logger.debug(
                    "reversal: no best_ask on %s side for market_id=%s", side, market.id
                )
                continue

            entry_price: float = p["entry_price"]
            take_profit_price: float = p["take_profit_price"]
            stop_loss_price: float = p["stop_loss_price"]
            min_price_distance: float = p["min_price_distance"]

            # Filter conditions
            if best_ask > entry_price:
                logger.debug(
                    "reversal: %s best_ask=%.4f > entry_price=%.4f — skipping market_id=%s",
                    side, best_ask, entry_price, market.id,
                )
                continue

            if best_ask <= stop_loss_price:
                logger.debug(
                    "reversal: %s best_ask=%.4f <= stop_loss=%.4f — skipping market_id=%s",
                    side, best_ask, stop_loss_price, market.id,
                )
                continue

            if take_profit_price <= best_ask:
                logger.debug(
                    "reversal: %s take_profit=%.4f <= best_ask=%.4f — no room to profit, "
                    "skipping market_id=%s",
                    side, take_profit_price, best_ask, market.id,
                )
                continue

            price_distance = take_profit_price - best_ask
            if price_distance < min_price_distance:
                logger.debug(
                    "reversal: %s price_distance=%.4f < min=%.4f — too tight, "
                    "skipping market_id=%s",
                    side, price_distance, min_price_distance, market.id,
                )
                continue

            # Confidence: deeper below entry → higher confidence, capped at 0.9
            raw_confidence = (entry_price - best_ask) / entry_price if entry_price > 0 else 0.0
            confidence = min(raw_confidence, 0.9)

            potential_gain_pct = (
                (take_profit_price - best_ask) / best_ask * 100.0
                if best_ask > 0
                else 0.0
            )

            meta: dict[str, Any] = {
                "best_ask": best_ask,
                "entry_price": entry_price,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
                "potential_gain_pct": round(potential_gain_pct, 2),
                "market_hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
            }

            target_price = max(0.001, min(0.999, best_ask))

            signals.append(
                TradeSignal(
                    market_id=market.id,
                    side=side,
                    target_price=round(target_price, 4),
                    size_usd=p["size_usd"],
                    confidence=round(confidence, 4),
                    exit_strategy=p["exit_strategy"],
                    exit_params={"target_pct": p["sell_target_pct"]},
                    metadata=meta,
                    strategy=self.name,
                )
            )

            logger.info(
                "reversal: market_id=%s %s@%.4f tp=%.4f confidence=%.3f gain=%.1f%%",
                market.id, side, target_price, take_profit_price,
                confidence, potential_gain_pct,
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise reversal parameters."""
        p = self._merge_params(params)

        p["entry_price"] = float(p["entry_price"])
        p["take_profit_price"] = float(p["take_profit_price"])
        p["stop_loss_price"] = float(p["stop_loss_price"])
        p["size_usd"] = float(p["size_usd"])
        p["max_cycles"] = int(p["max_cycles"])
        p["min_price_distance"] = float(p["min_price_distance"])
        p["min_market_hours_remaining"] = float(p["min_market_hours_remaining"])
        p["sell_target_pct"] = float(p["sell_target_pct"])

        if not 0.0 < p["entry_price"] <= 1.0:
            raise ValueError(f"entry_price must be in (0, 1], got {p['entry_price']}")
        if not 0.0 < p["take_profit_price"] <= 1.0:
            raise ValueError(
                f"take_profit_price must be in (0, 1], got {p['take_profit_price']}"
            )
        if not 0.0 <= p["stop_loss_price"] < p["entry_price"]:
            raise ValueError(
                f"stop_loss_price must be in [0, entry_price), "
                f"got {p['stop_loss_price']} vs entry={p['entry_price']}"
            )
        if p["take_profit_price"] <= p["entry_price"]:
            raise ValueError(
                f"take_profit_price ({p['take_profit_price']}) must be > "
                f"entry_price ({p['entry_price']})"
            )
        if p["size_usd"] <= 0:
            raise ValueError(f"size_usd must be positive, got {p['size_usd']}")
        if p["max_cycles"] < 1:
            raise ValueError(f"max_cycles must be >= 1, got {p['max_cycles']}")
        if p["min_price_distance"] < 0:
            raise ValueError(
                f"min_price_distance must be >= 0, got {p['min_price_distance']}"
            )
        if p["sell_target_pct"] <= 0:
            raise ValueError(
                f"sell_target_pct must be positive, got {p['sell_target_pct']}"
            )

        return p


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


class ReversalAggressiveStrategy(ReversalStrategy):
    """Aggressive reversal: higher entry, higher take-profit, tighter stop."""

    name = "reversal_aggressive"
    description = (
        "Aggressive mean-reversion: enters near fair-value dips (0.50), "
        "targets 0.65 exit, stop at 0.35"
    )
    default_params: dict[str, Any] = {
        **ReversalStrategy.default_params,
        "entry_price": 0.50,
        "take_profit_price": 0.65,
        "stop_loss_price": 0.35,
        "sell_target_pct": 30.0,
    }


class ReversalDeepStrategy(ReversalStrategy):
    """Deep-value reversal: enters only at heavily discounted prices."""

    name = "reversal_deep"
    description = (
        "Deep-value mean-reversion: waits for extreme dips (0.30 entry), "
        "targets 0.50 exit for a ~66% gain"
    )
    default_params: dict[str, Any] = {
        **ReversalStrategy.default_params,
        "entry_price": 0.30,
        "take_profit_price": 0.50,
        "stop_loss_price": 0.15,
        "sell_target_pct": 66.0,
    }


class ReversalScalpStrategy(ReversalStrategy):
    """Scalp reversal: small tight trades for quick 12% gains."""

    name = "reversal_scalp"
    description = (
        "Scalp mean-reversion: small $10 orders near current price (0.55 entry), "
        "tight 0.62 target for quick 12% gains"
    )
    default_params: dict[str, Any] = {
        **ReversalStrategy.default_params,
        "entry_price": 0.55,
        "take_profit_price": 0.62,
        "stop_loss_price": 0.45,
        "size_usd": 10.0,
        "sell_target_pct": 12.0,
    }
