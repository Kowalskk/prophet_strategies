"""
Weather Fade Strategy — exploits short-term overconfidence in weather markets.

Based on Le (2026) "Decomposing Crowd Wisdom": weather markets are OVERCONFIDENT
at short horizons (calibration slopes 0.69-0.87 at <48h). Prices are too extreme
— they overshoot what climatological base rates justify.

The recalibration formula:
    p* = p^θ / (p^θ + (1-p)^θ)

When θ < 1 (overconfidence), recalibrated probability is MORE MODERATE than price:
  - Weather YES at 85¢ with θ=0.74 → true prob ≈ 75¢. The YES is overpriced by 10¢.
  - Weather YES at 15¢ with θ=0.74 → true prob ≈ 25¢. The NO is overpriced too.

Strategy: sell the overpriced extreme. In practice, since we can only BUY on
Polymarket, we buy the OPPOSITE side when one side is priced very high (>80¢),
because the other side at <20¢ is actually worth more than its price implies.

Wait — with θ<1, p*>p for p<0.5 and p*<p for p>0.5. So:
  - A 20¢ contract is UNDERPRICED (true ~25¢) → BUY the cheap side
  - An 80¢ contract is OVERPRICED (true ~75¢) → BUY the opposite (cheap) side

Both point to the same trade: buy the non-favourite in short-horizon weather.
"""

from __future__ import annotations

import logging
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


# Calibration slopes for weather by time-to-resolution (Le 2026, Table 3)
WEATHER_SLOPES: dict[str, float] = {
    "0-1h": 0.69,
    "1-3h": 0.84,
    "3-6h": 0.74,
    "6-12h": 0.87,
    "12-24h": 0.91,
    "24-48h": 0.97,
    "2d-1w": 1.20,   # underconfident at longer horizons — no edge
    "1w-1mo": 1.37,
}


def _estimate_slope(hours_to_close: float) -> float:
    """Return the weather calibration slope for the given horizon."""
    if hours_to_close < 1:
        return 0.69
    elif hours_to_close < 3:
        return 0.84
    elif hours_to_close < 6:
        return 0.74
    elif hours_to_close < 12:
        return 0.87
    elif hours_to_close < 24:
        return 0.91
    elif hours_to_close < 48:
        return 0.97
    else:
        return 1.20  # beyond 48h, weather becomes underconfident — not our target


class WeatherFadeStrategy(StrategyBase):
    """Buy the non-favourite in short-horizon weather markets (overconfidence fade)."""

    name = "weather_fade"
    description = (
        "Buys the cheap side in short-horizon weather markets where Le (2026) shows "
        "prices are overconfident (θ<1). A 15¢ contract with θ=0.74 is truly worth ~25¢. "
        "Best edge at 3-6h (θ=0.74) and 0-1h (θ=0.69)."
    )
    default_params: dict[str, Any] = {
        "max_price": 0.25,          # buy the cheap side (non-favourite) priced below this
        "min_edge": 0.04,           # minimum recalibrated edge (true_prob - price)
        "capital_per_signal": 25.0,
        "max_hours_to_close": 48,   # only profitable at short horizons (<48h)
        "min_hours_to_close": 0.5,  # skip markets about to resolve (slippage risk)
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 80.0,    # sell when price rises ~80% toward true value
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self.validate_params(params)

        # Check category — weather or science (which includes weather on Polymarket)
        category = getattr(market, "category", None)
        if category not in ("weather", "science"):
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

        # Only exploit overconfidence (slope < 1.0)
        if slope >= 1.0:
            return []

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

            # Only buy cheap side (the non-favourite)
            if price > p["max_price"]:
                continue

            # Recalibrate — with θ<1, cheap side is underpriced
            true_prob = recalibrate(price, slope)
            edge = true_prob - price

            if edge < p["min_edge"]:
                continue

            confidence = min(0.85, 0.4 + edge * 4.0)

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
                    "overconfidence": round(1.0 - slope, 3),
                },
                strategy=self.name,
            ))

            logger.info(
                "%s: market_id=%d %s@%.3f → true=%.3f edge=+%.3f (θ=%.2f, %.1fh to close)",
                self.name, market.id, side_name.upper(), price, true_prob,
                edge, slope, hours_to_close,
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        for k in ("max_price", "min_edge", "capital_per_signal", "sell_target_pct"):
            p[k] = float(p[k])
        p["max_hours_to_close"] = float(p["max_hours_to_close"])
        p["min_hours_to_close"] = float(p["min_hours_to_close"])
        return p


class WeatherFadeAggressive(WeatherFadeStrategy):
    """Lower edge threshold, wider price range."""
    name = "weather_fade_aggr"
    description = "Weather fade: lower min_edge (2¢), wider max_price (30¢), $35 capital"
    default_params = {**WeatherFadeStrategy.default_params,
        "min_edge": 0.02, "max_price": 0.30, "capital_per_signal": 35.0}


class WeatherFadeConservative(WeatherFadeStrategy):
    """Higher edge threshold, only the most extreme overconfidence."""
    name = "weather_fade_cons"
    description = "Weather fade: high min_edge (6¢), max 24h horizon, $15 capital"
    default_params = {**WeatherFadeStrategy.default_params,
        "min_edge": 0.06, "max_hours_to_close": 24.0, "capital_per_signal": 15.0}


ALL_WEATHER_FADE_CLASSES = [
    WeatherFadeStrategy,
    WeatherFadeAggressive,
    WeatherFadeConservative,
]
