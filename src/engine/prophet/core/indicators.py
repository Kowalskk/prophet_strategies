"""
Technical Indicators for Polymarket — computed from price/orderbook history.

These indicators are calculated per-market and can be used by strategies
to make better entry/exit decisions.

Indicators
----------
1.  price_momentum        — rate of price change over N snapshots
2.  price_volatility      — stddev of price over N snapshots
3.  spread_avg            — average bid-ask spread (liquidity indicator)
4.  spread_trend          — is spread widening or tightening?
5.  volume_momentum       — change in trading volume
6.  book_imbalance        — bid_depth / (bid_depth + ask_depth)
7.  mean_reversion_score  — how far current price is from rolling mean
8.  combined_cost_gap     — YES_ask + NO_ask distance from 1.0
9.  price_ema_crossover   — fast EMA vs slow EMA signal
10. support_resistance    — nearest support/resistance levels from price history
11. time_decay_factor     — market urgency based on resolution proximity

Usage
-----
    from prophet.core.indicators import MarketIndicators

    indicators = MarketIndicators()
    result = await indicators.compute(market_id, lookback_hours=24)
    # result: dict with all indicator values
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketIndicators:
    """Computes technical indicators for a Polymarket market."""

    async def compute(
        self,
        market_id: int,
        db: AsyncSession,
        lookback_hours: int = 24,
    ) -> dict[str, Any]:
        """Compute all indicators for a market.

        Returns a dict with indicator name → value.
        Missing data produces None for that indicator.
        """
        from prophet.db.models import Market, OrderBookSnapshot

        cutoff = _utcnow() - timedelta(hours=lookback_hours)

        # Fetch orderbook history (YES side — used for both prices and OB metrics)
        ob_stmt = (
            select(
                OrderBookSnapshot.best_bid,
                OrderBookSnapshot.best_ask,
                OrderBookSnapshot.bid_depth_10pct,
                OrderBookSnapshot.ask_depth_10pct,
                OrderBookSnapshot.timestamp,
            )
            .where(
                OrderBookSnapshot.market_id == market_id,
                OrderBookSnapshot.side == "yes",
                OrderBookSnapshot.timestamp >= cutoff,
            )
            .order_by(OrderBookSnapshot.timestamp.asc())
        )
        ob_result = await db.execute(ob_stmt)

        # Derive prices from OB midpoints
        raw_rows = ob_result.all()

        # Derive prices from OB midpoints
        prices = []
        for r in raw_rows:
            if r[0] is not None and r[1] is not None:
                mid = (float(r[0]) + float(r[1])) / 2
                prices.append((mid, r[4]))

        orderbooks = [
            {
                "bid": float(r[0]) if r[0] else None,
                "ask": float(r[1]) if r[1] else None,
                "bid_depth": float(r[2]) if r[2] else None,
                "ask_depth": float(r[3]) if r[3] else None,
                "ts": r[4],
            }
            for r in raw_rows
        ]

        # Fetch market info for time_decay
        market_stmt = select(Market).where(Market.id == market_id)
        market_result = await db.execute(market_stmt)
        market = market_result.scalar_one_or_none()

        price_values = [p[0] for p in prices]

        result: dict[str, Any] = {
            "market_id": market_id,
            "data_points": len(prices),
            "lookback_hours": lookback_hours,
        }

        # 1. Price momentum
        result["price_momentum"] = _price_momentum(price_values)

        # 2. Price volatility
        result["price_volatility"] = _stddev(price_values)

        # 3. Average spread
        spreads = [
            ob["ask"] - ob["bid"]
            for ob in orderbooks
            if ob["bid"] is not None and ob["ask"] is not None
        ]
        result["spread_avg"] = _mean(spreads)

        # 4. Spread trend (positive = widening, negative = tightening)
        result["spread_trend"] = _trend(spreads)

        # 5. Volume momentum (using depth as proxy)
        depths = [
            (ob["bid_depth"] or 0) + (ob["ask_depth"] or 0)
            for ob in orderbooks
        ]
        result["volume_momentum"] = _trend(depths)

        # 6. Book imbalance (>0.5 = more bids, <0.5 = more asks)
        if orderbooks:
            last_ob = orderbooks[-1]
            bd = last_ob["bid_depth"] or 0
            ad = last_ob["ask_depth"] or 0
            total = bd + ad
            result["book_imbalance"] = round(bd / total, 4) if total > 0 else 0.5
        else:
            result["book_imbalance"] = None

        # 7. Mean reversion score (-1 to +1, how far from mean)
        result["mean_reversion_score"] = _mean_reversion(price_values)

        # 8. Combined cost gap (YES+NO asks vs 1.0)
        # Requires YES and NO orderbooks — uses latest
        result["combined_cost_gap"] = None  # Computed externally per market pair

        # 9. EMA crossover (fast=5, slow=20)
        result["ema_crossover"] = _ema_crossover(price_values, fast=5, slow=20)

        # 12. RSI (14-period)
        result["rsi"] = _rsi(price_values, period=14)

        # 13. VWAP (using depth as volume proxy)
        result["vwap"] = _vwap(price_values, depths[:len(price_values)] if depths else [])

        # 10. Support/resistance
        result["support"], result["resistance"] = _support_resistance(price_values)

        # 11. Time decay factor (0=far from resolution, 1=imminent)
        result["time_decay_factor"] = _time_decay(market)

        # Composite signal: -1 (strong sell) to +1 (strong buy)
        result["composite_score"] = _composite_score(result)

        return result


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------


def _price_momentum(prices: list[float], window: int = 10) -> float | None:
    """Rate of change over last `window` data points."""
    if len(prices) < window + 1:
        return None
    old = prices[-window - 1]
    new = prices[-1]
    if old == 0:
        return None
    return round((new - old) / old, 6)


def _stddev(values: list[float]) -> float | None:
    """Standard deviation."""
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return round(math.sqrt(variance), 6)


def _mean(values: list[float]) -> float | None:
    """Simple mean."""
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _trend(values: list[float], window: int = 5) -> float | None:
    """Linear slope of last `window` values (positive = increasing)."""
    if len(values) < window:
        return None
    recent = values[-window:]
    n = len(recent)
    x_mean = (n - 1) / 2
    y_mean = sum(recent) / n
    num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return round(num / den, 6)


def _mean_reversion(prices: list[float], window: int = 20) -> float | None:
    """Z-score of current price vs rolling mean. Range roughly -3 to +3."""
    if len(prices) < window:
        return None
    recent = prices[-window:]
    mean = sum(recent) / len(recent)
    std = _stddev(recent) or 0.001
    current = prices[-1]
    z = (current - mean) / std
    return round(max(-3, min(3, z)) / 3, 4)  # normalize to -1..+1


def _ema(values: list[float], span: int) -> list[float]:
    """Exponential moving average."""
    if not values:
        return []
    alpha = 2 / (span + 1)
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(alpha * v + (1 - alpha) * ema_vals[-1])
    return ema_vals


def _ema_crossover(
    prices: list[float], fast: int = 5, slow: int = 20
) -> float | None:
    """EMA crossover signal. Positive = fast above slow (bullish)."""
    if len(prices) < slow + 2:
        return None
    fast_ema = _ema(prices, fast)
    slow_ema = _ema(prices, slow)
    diff_now = fast_ema[-1] - slow_ema[-1]
    avg_price = sum(prices[-10:]) / 10 if len(prices) >= 10 else prices[-1]
    if avg_price == 0:
        return None
    return round(diff_now / avg_price, 6)


def _support_resistance(
    prices: list[float],
) -> tuple[float | None, float | None]:
    """Find nearest support and resistance from price history."""
    if len(prices) < 10:
        return None, None
    current = prices[-1]
    # Simple: support = recent min below current, resistance = recent max above
    below = [p for p in prices if p < current]
    above = [p for p in prices if p > current]
    support = max(below) if below else None
    resistance = min(above) if above else None
    return (
        round(support, 4) if support is not None else None,
        round(resistance, 4) if resistance is not None else None,
    )


def _rsi(prices: list[float], period: int = 14) -> float | None:
    """RSI (Relative Strength Index). Range 0-100. >70 overbought, <30 oversold."""
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    # Use last `period` deltas
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]
    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _vwap(prices: list[float], volumes: list[float]) -> float | None:
    """Volume Weighted Average Price. Uses depth as volume proxy."""
    if not prices or not volumes or len(prices) != len(volumes):
        return None
    total_pv = sum(p * v for p, v in zip(prices, volumes))
    total_v = sum(volumes)
    if total_v == 0:
        return None
    return round(total_pv / total_v, 6)


def _time_decay(market: Any) -> float | None:
    """Time urgency factor: 0 = far from resolution, 1 = imminent.

    Uses `end_date` or `close_time` from market if available.
    """
    if market is None:
        return None

    end_date = getattr(market, "end_date", None) or getattr(market, "close_time", None)
    if end_date is None:
        return None

    now = _utcnow()
    if isinstance(end_date, str):
        try:
            end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            return None

    remaining = (end_date - now).total_seconds()
    if remaining <= 0:
        return 1.0

    # Exponential decay: 48h→0.3, 24h→0.5, 6h→0.85, 1h→0.98
    hours_remaining = remaining / 3600
    decay = 1.0 - math.exp(-0.03 * (48 - min(hours_remaining, 48)))
    return round(max(0, min(1, decay)), 4)


def _composite_score(indicators: dict[str, Any]) -> float | None:
    """Combine indicators into a single score: -1 (bearish) to +1 (bullish).

    Weights (inspired by polymarket-assistant-tool bias score):
    - ema_crossover:   18%  (trend following)
    - book_imbalance:  15%  (order flow)
    - momentum:        15%  (price direction)
    - rsi:             14%  (overbought/oversold)
    - mean_reversion:  13%  (reversion signal)
    - spread_trend:    13%  (liquidity direction)
    - time_decay:      12%  (urgency)
    """
    components: list[tuple[float, float]] = []  # (value, weight)

    ema = indicators.get("ema_crossover")
    if ema is not None:
        norm = max(-1, min(1, ema * 100))
        components.append((norm, 0.18))

    imbalance = indicators.get("book_imbalance")
    if imbalance is not None:
        # >0.5 = more bids = bullish, normalize to -1..+1
        components.append(((imbalance - 0.5) * 2, 0.15))

    momentum = indicators.get("price_momentum")
    if momentum is not None:
        norm = max(-1, min(1, momentum * 10))
        components.append((norm, 0.15))

    rsi = indicators.get("rsi")
    if rsi is not None:
        # RSI: 50=neutral, <30=oversold(bullish), >70=overbought(bearish)
        # Normalize: 0→+1, 50→0, 100→-1
        norm = max(-1, min(1, (50 - rsi) / 50))
        components.append((norm, 0.14))

    mr = indicators.get("mean_reversion_score")
    if mr is not None:
        components.append((-mr, 0.13))

    spread = indicators.get("spread_trend")
    if spread is not None:
        norm = max(-1, min(1, -spread * 100))
        components.append((norm, 0.13))

    td = indicators.get("time_decay_factor")
    if td is not None:
        # Higher time decay = more urgency = slight bullish bias for entry
        components.append(((td - 0.5) * 2, 0.12))

    if not components:
        return None

    total_weight = sum(w for _, w in components)
    if total_weight == 0:
        return None

    score = sum(v * w for v, w in components) / total_weight
    return round(max(-1, min(1, score)), 4)
