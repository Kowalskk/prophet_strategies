"""
Volatility Spread Strategy — symmetric YES/NO limit orders around mid price.

Places two limit orders (YES and NO) at ``spread_percent`` below the current
mid price of each side.  The idea: both sides of a binary market sum to 1.0,
so buying both cheaply captures value if there is any bid/ask spread.

Default parameters
------------------
- ``spread_percent``  : 5.0 — how far below mid price to place each order (%)
- ``entry_price_max`` : 0.05 — never pay more than this per share (avoids
                         buying into already-elevated markets)
- ``capital_per_side``: 50.0 — USD deployed per YES order and per NO order
- ``exit_strategy``   : ``"sell_at_target"``
- ``sell_target_pct`` : 100.0 — exit at 2× entry (100% gain on the position)

Logic
-----
1. Get YES mid price from order book.
2. Infer NO mid price as ``1 - yes_mid`` (binary market identity).
3. ``target_yes = yes_mid * (1 - spread_percent / 100)``
4. ``target_no  = no_mid  * (1 - spread_percent / 100)``
5. Skip if either target > entry_price_max.
6. Return two TradeSignal instances.
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class VolatilitySpreadStrategy(StrategyBase):
    """Symmetric YES/NO orders capturing bidirectional volatility."""

    name = "volatility_spread"
    description = (
        "Places symmetric YES and NO limit orders below the current mid price, "
        "targeting a 2× exit. Profits when either side resolves in our favour "
        "or when liquidity improves and the price rises."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 5.0,      # % below mid price to place orders
        "entry_price_max": 0.05,    # max price per share (skip if mid - spread > this)
        "capital_per_side": 50.0,   # USD per YES order and per NO order
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 100.0,   # sell when price doubles (100% gain)
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Evaluate market and return YES + NO signals if conditions are met."""
        p = self.validate_params(params)

        yes_book = orderbook.get("yes")
        if yes_book is None:
            logger.debug(
                "volatility_spread: no YES order book for market_id=%s", market.id
            )
            return []

        # Get YES mid price
        yes_mid = getattr(yes_book, "mid_price", None)
        if yes_mid is None:
            # Try computing from best_bid / best_ask
            best_bid = getattr(yes_book, "best_bid", None)
            best_ask = getattr(yes_book, "best_ask", None)
            if best_bid is not None and best_ask is not None:
                yes_mid = (best_bid + best_ask) / 2.0
            else:
                logger.debug(
                    "volatility_spread: cannot determine YES mid price for market_id=%s",
                    market.id,
                )
                return []

        # Sanity check
        if not 0.0 < yes_mid < 1.0:
            logger.debug(
                "volatility_spread: YES mid price out of range (%.4f) for market_id=%s",
                yes_mid, market.id,
            )
            return []

        no_mid = 1.0 - yes_mid
        spread_frac = p["spread_percent"] / 100.0

        # Target order prices (below mid by spread_percent)
        target_yes = yes_mid * (1.0 - spread_frac)
        target_no = no_mid * (1.0 - spread_frac)

        # Skip if either target would be above the max entry price
        if target_yes > p["entry_price_max"]:
            logger.debug(
                "volatility_spread: target_yes=%.4f > entry_price_max=%.4f — skipping market_id=%s",
                target_yes, p["entry_price_max"], market.id,
            )
            return []

        if target_no > p["entry_price_max"]:
            logger.debug(
                "volatility_spread: target_no=%.4f > entry_price_max=%.4f — skipping market_id=%s",
                target_no, p["entry_price_max"], market.id,
            )
            return []

        # Safety: ensure prices are in valid range
        target_yes = max(0.001, min(0.999, target_yes))
        target_no = max(0.001, min(0.999, target_no))

        meta = {
            "yes_mid": yes_mid,
            "no_mid": no_mid,
            "spread_percent": p["spread_percent"],
            "combined_cost": target_yes + target_no,
        }

        exit_params = {"target_pct": p["sell_target_pct"]}

        signals: list[TradeSignal] = [
            TradeSignal(
                market_id=market.id,
                side="YES",
                target_price=round(target_yes, 4),
                size_usd=p["capital_per_side"],
                confidence=0.7,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata=meta,
                strategy=self.name,
            ),
            TradeSignal(
                market_id=market.id,
                side="NO",
                target_price=round(target_no, 4),
                size_usd=p["capital_per_side"],
                confidence=0.7,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata=meta,
                strategy=self.name,
            ),
        ]

        logger.info(
            "volatility_spread: market_id=%d YES@%.4f NO@%.4f (combined=%.4f)",
            market.id, target_yes, target_no, target_yes + target_no,
        )
        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise volatility_spread parameters."""
        p = self._merge_params(params)

        p["spread_percent"] = float(p["spread_percent"])
        p["entry_price_max"] = float(p["entry_price_max"])
        p["capital_per_side"] = float(p["capital_per_side"])
        p["sell_target_pct"] = float(p["sell_target_pct"])

        if not 0.0 < p["spread_percent"] < 100.0:
            raise ValueError(
                f"spread_percent must be in (0, 100), got {p['spread_percent']}"
            )
        if not 0.0 < p["entry_price_max"] <= 1.0:
            raise ValueError(
                f"entry_price_max must be in (0, 1], got {p['entry_price_max']}"
            )
        if p["capital_per_side"] <= 0:
            raise ValueError(
                f"capital_per_side must be positive, got {p['capital_per_side']}"
            )
        if p["sell_target_pct"] <= 0:
            raise ValueError(
                f"sell_target_pct must be positive, got {p['sell_target_pct']}"
            )

        return p
