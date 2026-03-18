"""
Stink Bid Strategy — ultra-cheap limit orders on extreme outcomes.

Places tier-1 and tier-2 limit orders on both YES and NO sides at very low
prices (3¢ and 0.5¢).  The logic: occasionally market panic or illiquidity
causes prices to temporarily dip to these levels, yielding 20-200× payoffs.

Default parameters
------------------
- ``tier1_price``   : 0.03 — first tier entry price (3¢)
- ``tier1_capital`` : 50.0 — USD to deploy at tier 1
- ``tier2_price``   : 0.005 — second tier entry price (0.5¢)
- ``tier2_capital`` : 3.0 — USD to deploy at tier 2
- ``exit_strategy`` : ``"hold_to_resolution"`` — these are long-duration bets

Logic
-----
For each tier (1 and 2) and each side (YES and NO):
1. Get the current best_ask for that side.
2. Skip this tier/side if ``best_ask <= tier_price`` — the market is already
   at or below our target (we'd be buying at the wrong fill price).
3. Otherwise, emit a signal.

Returns up to 4 signals: tier1_YES, tier1_NO, tier2_YES, tier2_NO.
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class StinkBidStrategy(StrategyBase):
    """Ultra-cheap limit orders on extreme outcomes for high-multiplier payoffs."""

    name = "stink_bid"
    description = (
        "Places aggressive low-price limit orders (3¢ and 0.5¢) on both sides. "
        "Filled only during liquidity crunches. Held to resolution for maximum payout."
    )
    default_params: dict[str, Any] = {
        "tier1_price": 0.03,      # 3¢ — primary stink bid
        "tier1_capital": 50.0,    # USD at tier 1
        "tier2_price": 0.005,     # 0.5¢ — ultra deep stink bid
        "tier2_capital": 3.0,     # USD at tier 2
        "exit_strategy": "hold_to_resolution",
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Return stink bid signals for all viable tier/side combinations."""
        p = self.validate_params(params)

        yes_book = orderbook.get("yes")
        no_book = orderbook.get("no")

        # Extract best asks (the price we'd have to pay to buy immediately)
        yes_best_ask = _get_best_ask(yes_book)
        no_best_ask = _get_best_ask(no_book)

        tiers = [
            ("tier1", p["tier1_price"], p["tier1_capital"]),
            ("tier2", p["tier2_price"], p["tier2_capital"]),
        ]
        sides = [
            ("YES", yes_best_ask),
            ("NO", no_best_ask),
        ]

        signals: list[TradeSignal] = []

        for tier_name, tier_price, tier_capital in tiers:
            for side, best_ask in sides:
                if best_ask is None:
                    logger.debug(
                        "stink_bid: no %s best_ask for market_id=%s — skipping %s %s",
                        side, market.id, tier_name, side,
                    )
                    continue

                # Skip if market is already at or below our target price
                # (we'd fill immediately at an unfavourable price)
                if best_ask <= tier_price:
                    logger.debug(
                        "stink_bid: market_id=%d %s %s best_ask=%.4f <= tier_price=%.4f — skipping",
                        market.id, side, tier_name, best_ask, tier_price,
                    )
                    continue

                signals.append(
                    TradeSignal(
                        market_id=market.id,
                        side=side,
                        target_price=tier_price,
                        size_usd=tier_capital,
                        confidence=0.5,
                        exit_strategy=p["exit_strategy"],
                        exit_params={},
                        metadata={
                            "tier": tier_name,
                            "best_ask": best_ask,
                            "potential_multiplier": round(1.0 / tier_price, 1),
                        },
                        strategy=self.name,
                    )
                )

        if signals:
            logger.info(
                "stink_bid: market_id=%d — %d signal(s) generated",
                market.id, len(signals),
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise stink_bid parameters."""
        p = self._merge_params(params)

        p["tier1_price"] = float(p["tier1_price"])
        p["tier1_capital"] = float(p["tier1_capital"])
        p["tier2_price"] = float(p["tier2_price"])
        p["tier2_capital"] = float(p["tier2_capital"])

        if not 0.0 < p["tier1_price"] <= 1.0:
            raise ValueError(f"tier1_price must be in (0, 1], got {p['tier1_price']}")
        if not 0.0 < p["tier2_price"] <= 1.0:
            raise ValueError(f"tier2_price must be in (0, 1], got {p['tier2_price']}")
        if p["tier1_price"] <= p["tier2_price"]:
            raise ValueError(
                f"tier1_price ({p['tier1_price']}) must be > tier2_price ({p['tier2_price']})"
            )
        if p["tier1_capital"] <= 0:
            raise ValueError(f"tier1_capital must be positive, got {p['tier1_capital']}")
        if p["tier2_capital"] <= 0:
            raise ValueError(f"tier2_capital must be positive, got {p['tier2_capital']}")

        return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_best_ask(book: Any | None) -> float | None:
    """Return the best ask price from an OrderBook or None if unavailable."""
    if book is None:
        return None
    # Try attribute access (OrderBook pydantic model)
    best_ask = getattr(book, "best_ask", None)
    if best_ask is not None:
        return float(best_ask)
    # Try asks list
    asks = getattr(book, "asks", None)
    if asks:
        return float(asks[0].price)
    return None
