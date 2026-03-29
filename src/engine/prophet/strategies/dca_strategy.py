"""
DCA Strategy — dollar-cost averaging into dips, exiting on recovery.

Places an initial order at ``anchor_price`` then ladders additional buy orders at
increasing sizes as the price falls below the anchor.  The strategy generates ONE
signal per evaluation — for the current DCA level the market is at — rather than
flooding the order queue with all levels simultaneously.

Default parameters
------------------
- ``anchor_price``             : 0.55  — initial entry target price
- ``dca_level_spacing``        : 0.05  — price gap between DCA levels
- ``level_spacing_multiplier`` : 1.0   — 1.0=even, <1=compress, >1=spread
- ``dca_levels``               : 3     — number of DCA steps below anchor
- ``initial_size_usd``         : 10.0  — first order size
- ``dca_size_multiplier``      : 1.4   — each step is this times the previous
- ``take_profit_above_avg``    : 0.05  — take profit 5c above average cost
- ``side``                     : "YES"
- ``min_market_hours_remaining``: 4.0  — skip markets resolving soon
- ``exit_strategy``            : "sell_at_target"
- ``sell_target_pct``          : 15.0

Logic
-----
1. Check market has at least ``min_market_hours_remaining`` hours until resolution.
2. Get best_ask for the given side.
3. If best_ask > anchor_price: skip (wait for price to fall to anchor).
4. Calculate current DCA level based on distance below anchor:
   - Level 0: best_ask in [anchor - spacing, anchor]
   - Level 1: best_ask in [anchor - 2*spacing, anchor - spacing)
   - … up to ``dca_levels``
5. Generate ONE signal for the current level:
   - target_price = anchor - (level * spacing * multiplier)
   - size_usd     = initial_size_usd * (dca_size_multiplier ** level)
   - confidence   = min(0.5 + level * 0.1, 0.85)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class DCAStrategy(StrategyBase):
    """Dollar-cost averaging: ladders buy orders as price drops, exits on recovery."""

    name = "dca"
    description = (
        "Dollar-cost averaging: ladders buy orders as price drops, "
        "exits on recovery above average cost"
    )
    default_params: dict[str, Any] = {
        "anchor_price": 0.55,               # initial entry target price
        "dca_level_spacing": 0.05,          # price gap between DCA levels
        "level_spacing_multiplier": 1.0,    # 1.0=even, 0.5=compress, 1.5=spread
        "dca_levels": 3,                    # how many DCA steps below anchor
        "initial_size_usd": 10.0,           # first order size
        "dca_size_multiplier": 1.4,         # each step is this times the previous
        "take_profit_above_avg": 0.05,      # take profit 5c above average cost (informational)
        "side": "YES",
        "min_market_hours_remaining": 4.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 15.0,
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Evaluate market and return a single DCA level signal if conditions are met."""
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
                "dca: market_id=%s resolves in %.1fh < %.1fh min — skipping",
                market.id, hours_remaining, p["min_market_hours_remaining"],
            )
            return []

        # --- order book ------------------------------------------------------
        side: str = str(p["side"]).upper()
        if side not in ("YES", "NO"):
            logger.warning(
                "dca: unknown side=%r for market_id=%s — skipping", p["side"], market.id
            )
            return []

        book = orderbook.get(side.lower())
        if book is None:
            logger.debug("dca: no %s order book for market_id=%s", side, market.id)
            return []

        best_ask = getattr(book, "best_ask", None)
        if best_ask is None:
            logger.debug(
                "dca: no best_ask on %s side for market_id=%s", side, market.id
            )
            return []

        anchor_price: float = p["anchor_price"]

        # Wait for price to reach or fall below anchor
        if best_ask > anchor_price:
            logger.debug(
                "dca: %s best_ask=%.4f > anchor=%.4f — waiting, skipping market_id=%s",
                side, best_ask, anchor_price, market.id,
            )
            return []

        # --- determine current DCA level -------------------------------------
        spacing: float = p["dca_level_spacing"] * p["level_spacing_multiplier"]
        dca_levels: int = p["dca_levels"]

        current_level: int = 0
        for lvl in range(dca_levels + 1):
            lower_bound = anchor_price - (lvl + 1) * spacing
            upper_bound = anchor_price - lvl * spacing
            if lower_bound <= best_ask <= upper_bound:
                current_level = lvl
                break
        else:
            # Price is below all defined levels — treat as the deepest level
            current_level = dca_levels

        # --- build signal for current level ----------------------------------
        target_price_raw = anchor_price - current_level * spacing
        target_price = max(0.001, min(0.999, target_price_raw))

        # Use best_ask as the actual order price if it is more attractive
        order_price = min(best_ask, target_price)
        order_price = max(0.001, min(0.999, order_price))

        size_usd: float = p["initial_size_usd"] * (p["dca_size_multiplier"] ** current_level)

        # Confidence increases with depth (deeper = more confident in mean reversion)
        confidence = min(0.5 + current_level * 0.1, 0.85)

        # Rough average cost estimate assuming all prior levels were filled at their midpoints
        avg_cost_estimate: float = anchor_price
        if current_level > 0:
            total_cost = 0.0
            total_shares = 0.0
            for lvl in range(current_level + 1):
                lvl_price = anchor_price - lvl * spacing
                lvl_size = p["initial_size_usd"] * (p["dca_size_multiplier"] ** lvl)
                shares = lvl_size / lvl_price if lvl_price > 0 else 0.0
                total_cost += lvl_size
                total_shares += shares
            avg_cost_estimate = total_cost / total_shares if total_shares > 0 else anchor_price

        meta: dict[str, Any] = {
            "level": current_level,
            "target_price": round(target_price, 4),
            "size_usd": round(size_usd, 2),
            "anchor_price": anchor_price,
            "best_ask": best_ask,
            "avg_cost_estimate": round(avg_cost_estimate, 4),
            "market_hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
        }

        signal = TradeSignal(
            market_id=market.id,
            side=side,
            target_price=round(order_price, 4),
            size_usd=round(size_usd, 2),
            confidence=round(confidence, 4),
            exit_strategy=p["exit_strategy"],
            exit_params={"target_pct": p["sell_target_pct"]},
            metadata=meta,
            strategy=self.name,
        )

        logger.info(
            "dca: market_id=%s %s level=%d order@%.4f size=$%.2f confidence=%.2f",
            market.id, side, current_level, order_price, size_usd, confidence,
        )
        return [signal]

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise DCA parameters."""
        p = self._merge_params(params)

        p["anchor_price"] = float(p["anchor_price"])
        p["dca_level_spacing"] = float(p["dca_level_spacing"])
        p["level_spacing_multiplier"] = float(p["level_spacing_multiplier"])
        p["dca_levels"] = int(p["dca_levels"])
        p["initial_size_usd"] = float(p["initial_size_usd"])
        p["dca_size_multiplier"] = float(p["dca_size_multiplier"])
        p["take_profit_above_avg"] = float(p["take_profit_above_avg"])
        p["min_market_hours_remaining"] = float(p["min_market_hours_remaining"])
        p["sell_target_pct"] = float(p["sell_target_pct"])

        if not 0.0 < p["anchor_price"] <= 1.0:
            raise ValueError(f"anchor_price must be in (0, 1], got {p['anchor_price']}")
        if p["dca_level_spacing"] <= 0:
            raise ValueError(
                f"dca_level_spacing must be positive, got {p['dca_level_spacing']}"
            )
        if p["level_spacing_multiplier"] <= 0:
            raise ValueError(
                f"level_spacing_multiplier must be positive, got {p['level_spacing_multiplier']}"
            )
        if p["dca_levels"] < 1:
            raise ValueError(f"dca_levels must be >= 1, got {p['dca_levels']}")
        if p["initial_size_usd"] <= 0:
            raise ValueError(
                f"initial_size_usd must be positive, got {p['initial_size_usd']}"
            )
        if p["dca_size_multiplier"] < 1.0:
            raise ValueError(
                f"dca_size_multiplier must be >= 1.0, got {p['dca_size_multiplier']}"
            )
        if p["sell_target_pct"] <= 0:
            raise ValueError(
                f"sell_target_pct must be positive, got {p['sell_target_pct']}"
            )
        # Ensure the deepest DCA level stays above zero
        effective_spacing = p["dca_level_spacing"] * p["level_spacing_multiplier"]
        deepest_price = p["anchor_price"] - p["dca_levels"] * effective_spacing
        if deepest_price <= 0:
            raise ValueError(
                f"DCA ladder goes to {deepest_price:.4f} at level {p['dca_levels']} — "
                f"reduce dca_levels or dca_level_spacing"
            )

        return p


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


class DCAConservativeStrategy(DCAStrategy):
    """Conservative DCA: lower anchor, smaller orders, tighter spacing."""

    name = "dca_conservative"
    description = (
        "Conservative DCA: lower anchor (0.45), small $5 seed orders, "
        "modest 1.2x size ramp, 4c take-profit"
    )
    default_params: dict[str, Any] = {
        **DCAStrategy.default_params,
        "anchor_price": 0.45,
        "dca_level_spacing": 0.04,
        "dca_levels": 3,
        "initial_size_usd": 5.0,
        "dca_size_multiplier": 1.2,
        "take_profit_above_avg": 0.04,
        "sell_target_pct": 10.0,
    }


class DCAAggressiveStrategy(DCAStrategy):
    """Aggressive DCA: high anchor, larger orders, wider spacing, more levels."""

    name = "dca_aggressive"
    description = (
        "Aggressive DCA: higher anchor (0.60), $15 seed orders, 1.5x size ramp, "
        "4 levels with 6c spacing, 6c take-profit"
    )
    default_params: dict[str, Any] = {
        **DCAStrategy.default_params,
        "anchor_price": 0.60,
        "dca_level_spacing": 0.06,
        "dca_levels": 4,
        "initial_size_usd": 15.0,
        "dca_size_multiplier": 1.5,
        "take_profit_above_avg": 0.06,
        "sell_target_pct": 12.0,
    }


class DCASportsStrategy(DCAStrategy):
    """Sports-market DCA: tuned for sports markets with wider price swings."""

    name = "dca_sports"
    description = (
        "Sports-market DCA: 0.50 anchor, $10 seed orders, 8c spacing, "
        "1.3x ramp, 8c take-profit — suited for volatile sports markets"
    )
    default_params: dict[str, Any] = {
        **DCAStrategy.default_params,
        "anchor_price": 0.50,
        "dca_level_spacing": 0.08,
        "dca_levels": 3,
        "initial_size_usd": 10.0,
        "dca_size_multiplier": 1.3,
        "take_profit_above_avg": 0.08,
        "sell_target_pct": 18.0,
    }
