"""
Political Favourite Strategy — exploits persistent underconfidence in political markets.

Based on Le (2026) "Decomposing Crowd Wisdom": political prediction market prices
are chronically compressed toward 50%. The calibration slope θ allows us to compute
the TRUE probability from the market price:

    p* = p^θ / (p^θ + (1-p)^θ)

At 2d-1w horizon, θ ≈ 1.83 for politics. A contract at 70¢ has true prob ≈ 83%.
Buying the favourite (the side priced higher) captures this systematic mispricing.

The strategy buys the leading side when:
  1. Market is political (category == "politics")
  2. One side is priced in the "favourite" range (min_price..max_price)
  3. The recalibrated edge (true_prob - market_price) exceeds min_edge
  4. Time to resolution falls within the profitable horizon window
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


def recalibrate(price: float, slope: float) -> float:
    """Apply Le (2026) recalibration: p* = p^θ / (p^θ + (1-p)^θ)."""
    if price <= 0.0 or price >= 1.0 or slope <= 0.0:
        return price
    p_theta = price ** slope
    q_theta = (1.0 - price) ** slope
    return p_theta / (p_theta + q_theta)


# Calibration slopes by time-to-resolution for politics (Le 2026, Table 3)
POLITICS_SLOPES: dict[str, float] = {
    "3-6h": 1.32,
    "6-12h": 1.55,
    "12-24h": 1.48,
    "24-48h": 1.52,
    "2d-1w": 1.83,
    "1w-1mo": 1.83,
    "1mo+": 1.73,
}


def _estimate_slope(hours_to_close: float) -> float:
    """Return the appropriate calibration slope for the given horizon."""
    if hours_to_close < 3:
        return 1.34  # 0-3h — still underconfident but less
    elif hours_to_close < 6:
        return 1.32
    elif hours_to_close < 12:
        return 1.55
    elif hours_to_close < 24:
        return 1.48
    elif hours_to_close < 48:
        return 1.52
    elif hours_to_close < 168:  # 1 week
        return 1.83
    elif hours_to_close < 720:  # 1 month
        return 1.83
    else:
        return 1.73


class PoliticalFavouriteStrategy(StrategyBase):
    """Buy the favourite in political markets to exploit underconfidence bias."""

    name = "political_favourite"
    description = (
        "Buys the leading side in political markets where Le (2026) recalibration "
        "reveals the true probability is significantly higher than the market price. "
        "Best edge at 2d-1w horizon (θ=1.83): 70¢ → 83¢ true = +13¢ edge."
    )
    default_params: dict[str, Any] = {
        "min_price": 0.55,          # only buy favourites priced above this
        "max_price": 0.85,          # avoid near-certain outcomes (low upside)
        "min_edge": 0.05,           # minimum recalibrated edge (true_prob - price)
        "capital_per_signal": 40.0, # USD per trade
        "min_hours_to_close": 6,    # skip markets resolving too soon
        "max_hours_to_close": 720,  # skip markets >1 month out (less reliable)
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 50.0,    # sell when price rises ~50% of the edge
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self.validate_params(params)

        # Check category
        category = getattr(market, "category", None)
        if category != "politics":
            return []

        # Estimate time to resolution
        end_date = getattr(market, "end_date", None)
        if not end_date:
            return []

        now = datetime.now(timezone.utc)
        if isinstance(end_date, str):
            try:
                end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return []

        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        hours_to_close = (end_date - now).total_seconds() / 3600.0
        if hours_to_close < p["min_hours_to_close"] or hours_to_close > p["max_hours_to_close"]:
            return []

        slope = _estimate_slope(hours_to_close)

        # Find the favourite side from orderbook
        signals: list[TradeSignal] = []

        for side_name in ("yes", "no"):
            book = orderbook.get(side_name)
            if book is None:
                continue

            best_ask = getattr(book, "best_ask", None)
            if best_ask is None:
                asks = getattr(book, "asks", None)
                if asks:
                    best_ask = float(asks[0].price)
            if best_ask is None:
                continue

            price = float(best_ask)

            # Only consider the favourite range
            if price < p["min_price"] or price > p["max_price"]:
                continue

            # Recalibrate
            true_prob = recalibrate(price, slope)
            edge = true_prob - price

            if edge < p["min_edge"]:
                logger.debug(
                    "%s: market_id=%d %s@%.3f → true=%.3f edge=%.3f < min %.3f",
                    self.name, market.id, side_name.upper(), price, true_prob, edge, p["min_edge"],
                )
                continue

            # Confidence scales with edge size
            confidence = min(0.9, 0.5 + edge * 3.0)

            exit_params = {"target_pct": p["sell_target_pct"]}

            signals.append(TradeSignal(
                market_id=market.id,
                side=side_name.upper(),
                target_price=price,
                size_usd=p["capital_per_signal"],
                confidence=confidence,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata={
                    "slope": slope,
                    "market_price": price,
                    "true_prob": round(true_prob, 4),
                    "edge": round(edge, 4),
                    "hours_to_close": round(hours_to_close, 1),
                    "horizon_bin": _horizon_label(hours_to_close),
                },
                strategy=self.name,
            ))

            logger.info(
                "%s: market_id=%d %s@%.3f → true=%.3f edge=+%.3f (θ=%.2f, %s)",
                self.name, market.id, side_name.upper(), price, true_prob,
                edge, slope, _horizon_label(hours_to_close),
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        for k in ("min_price", "max_price", "min_edge", "capital_per_signal",
                   "sell_target_pct"):
            p[k] = float(p[k])
        p["min_hours_to_close"] = float(p["min_hours_to_close"])
        p["max_hours_to_close"] = float(p["max_hours_to_close"])
        return p


class PoliticalFavouriteAggressive(PoliticalFavouriteStrategy):
    """Lower edge threshold, higher capital — for high-confidence political markets."""
    name = "political_favourite_aggr"
    description = "Political favourite: lower min_edge (3¢), higher capital ($60)"
    default_params = {**PoliticalFavouriteStrategy.default_params,
        "min_edge": 0.03, "capital_per_signal": 60.0, "min_price": 0.60}


class PoliticalFavouriteConservative(PoliticalFavouriteStrategy):
    """Higher edge threshold, smaller capital — only the clearest mispricings."""
    name = "political_favourite_cons"
    description = "Political favourite: high min_edge (8¢), smaller capital ($25)"
    default_params = {**PoliticalFavouriteStrategy.default_params,
        "min_edge": 0.08, "capital_per_signal": 25.0}


ALL_POLITICAL_FAVOURITE_CLASSES = [
    PoliticalFavouriteStrategy,
    PoliticalFavouriteAggressive,
    PoliticalFavouriteConservative,
]


def _horizon_label(hours: float) -> str:
    if hours < 6:
        return "3-6h"
    elif hours < 12:
        return "6-12h"
    elif hours < 24:
        return "12-24h"
    elif hours < 48:
        return "24-48h"
    elif hours < 168:
        return "2d-1w"
    elif hours < 720:
        return "1w-1mo"
    return "1mo+"
