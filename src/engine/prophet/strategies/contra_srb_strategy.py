"""
Contra-SRB Strategy — last-minute lottery on ultra-cheap OTM positions.

Logic
-----
In the last N hours before resolution, if the spot price is within X% of the
threshold, buy the cheap (OTM) side at ≤2¢. The idea: the market already
priced this as near-impossible, but there is still time for a spike.

Entry conditions:
  1. Market resolves within ``hours_before_res`` hours
  2. Spot is within ``max_distance_pct`` of the threshold (has a realistic chance)
  3. Best ask on the cheap side is ≤ ``max_entry_price``

Exit: hold to resolution (no target exit — the whole point is the lottery payout).

Backtest results (9,955 markets, Jun 2024–Mar 2026):
  csrb_48h_2c_p90: WR=2.2%, ROI=+482%  ← best config
  csrb_48h_2c_p80: WR=2.2%, ROI=+469%
  csrb_24h_2c_p50: WR=2.2%, ROI=+429%
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class ContraSRBStrategy(StrategyBase):
    """Last-minute lottery: buy ultra-cheap OTM positions near resolution."""

    name = "csrb_48h_2c_p90"
    description = (
        "Contra-SRB: buy ≤2¢ OTM positions in last 48h before resolution "
        "when spot is within 9.5% of threshold. Hold to resolution. "
        "Backtest: WR=2.2%, ROI=+482%."
    )
    default_params: dict[str, Any] = {
        "max_entry_price": 0.02,       # only buy if ask ≤ this (2¢)
        "hours_before_res": 48.0,      # enter only within last N hours
        "max_distance_pct": 9.5,       # spot must be within X% of threshold
        "capital": 10.0,               # USD per trade
    }

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        p["max_entry_price"] = float(p["max_entry_price"])
        p["hours_before_res"] = float(p["hours_before_res"])
        p["max_distance_pct"] = float(p["max_distance_pct"])
        p["capital"] = float(p["capital"])
        return p

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self._merge_params(params)
        max_entry = float(p["max_entry_price"])
        hours = float(p["hours_before_res"])
        max_dist = float(p["max_distance_pct"])
        capital = float(p["capital"])

        # --- Require threshold and resolution_time ---
        threshold = getattr(market, "threshold", None)
        resolution_time = getattr(market, "resolution_time", None)
        if threshold is None or not threshold or spot_price <= 0:
            return []
        # Fall back to resolution_date (end-of-day UTC) for active markets
        if resolution_time is None:
            resolution_date = getattr(market, "resolution_date", None)
            if resolution_date is None:
                return []
            from datetime import date, timedelta
            if isinstance(resolution_date, str):
                resolution_date = date.fromisoformat(resolution_date)
            # Markets resolve at ~midnight UTC of the following day
            resolution_time = datetime(
                resolution_date.year, resolution_date.month, resolution_date.day,
                23, 59, 59, tzinfo=timezone.utc
            )

        # --- Check time window ---
        now = datetime.now(timezone.utc)
        if resolution_time.tzinfo is None:
            resolution_time = resolution_time.replace(tzinfo=timezone.utc)
        hours_remaining = (resolution_time - now).total_seconds() / 3600.0
        if hours_remaining <= 0 or hours_remaining > hours:
            return []

        # --- Check spot distance ---
        dist_pct = abs(spot_price - threshold) / threshold * 100.0
        if dist_pct > max_dist:
            return []

        # --- Determine cheap (OTM) side ---
        direction = (getattr(market, "direction", None) or "ABOVE").upper()
        if direction != "ABOVE":
            return []

        cheap_side = "YES" if spot_price < threshold else "NO"

        # --- Check orderbook ---
        book = orderbook.get(cheap_side.lower())
        if book is None:
            return []

        best_ask = getattr(book, "best_ask", None)
        if best_ask is None:
            asks = getattr(book, "asks", None)
            if asks:
                best_ask = float(asks[0].price)
        if best_ask is None or best_ask > max_entry:
            return []

        logger.info(
            "%s: market_id=%s %s@%.4f dist=%.1f%% %.1fh before res → signal",
            self.name, market.id, cheap_side, best_ask, dist_pct, hours_remaining,
        )

        return [TradeSignal(
            market_id=market.id,
            side=cheap_side,
            target_price=best_ask,
            size_usd=capital,
            confidence=0.40,
            exit_strategy="hold_to_resolution",
            exit_params={},
            metadata={
                "spot_price": spot_price,
                "threshold": threshold,
                "distance_pct": round(dist_pct, 2),
                "hours_remaining": round(hours_remaining, 1),
                "best_ask": best_ask,
                "potential_multiplier": round(1.0 / best_ask, 1),
            },
            strategy=self.name,
        )]


# ---------------------------------------------------------------------------
# Variants — different windows, distance filters, entry prices
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 48h variants — SOL percentiles: p50=3.5% p60=4.5% p70=5.6% p75=6.3%
#                                  p80=7.1% p90=9.5% p95=11.9% p99=17.6%
# ---------------------------------------------------------------------------

class ContraSRB48h2cP50(ContraSRBStrategy):
    name = "csrb_48h_2c_p50"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 3.5% of threshold. ROI=+400%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 3.5}


class ContraSRB48h2cP60(ContraSRBStrategy):
    name = "csrb_48h_2c_p60"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 4.5% of threshold. ROI=+455%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 4.5}


class ContraSRB48h2cP70(ContraSRBStrategy):
    name = "csrb_48h_2c_p70"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 5.6% of threshold. ROI=+436%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 5.6}


class ContraSRB48h2cP75(ContraSRBStrategy):
    name = "csrb_48h_2c_p75"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 6.3% of threshold. ROI=+449%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 6.3}


class ContraSRB48h2cP80(ContraSRBStrategy):
    name = "csrb_48h_2c_p80"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 7.1% of threshold. ROI=+469%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 7.1}


class ContraSRB48h2cP95(ContraSRBStrategy):
    name = "csrb_48h_2c_p95"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 11.9% of threshold. ROI=+432%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 11.9}


class ContraSRB48h2cP99(ContraSRBStrategy):
    name = "csrb_48h_2c_p99"
    description = "Contra-SRB: ≤2¢, last 48h, spot within 17.6% of threshold. ROI=+357%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 48.0, "max_distance_pct": 17.6}


# ---------------------------------------------------------------------------
# 24h variants — SOL percentiles: p50=2.5% p60=3.1% p70=3.9% p75=4.4%
#                                  p80=5.0% p90=6.8% p95=8.4% p99=13.4%
# ---------------------------------------------------------------------------

class ContraSRB24h2cP50(ContraSRBStrategy):
    name = "csrb_24h_2c_p50"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 2.5% of threshold. ROI=+429%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 2.5}


class ContraSRB24h2cP60(ContraSRBStrategy):
    name = "csrb_24h_2c_p60"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 3.1% of threshold. ROI=+400%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 3.1}


class ContraSRB24h2cP70(ContraSRBStrategy):
    name = "csrb_24h_2c_p70"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 3.9% of threshold. ROI=+397%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 3.9}


class ContraSRB24h2cP75(ContraSRBStrategy):
    name = "csrb_24h_2c_p75"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 4.4% of threshold. ROI=+412%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 4.4}


class ContraSRB24h2cP80(ContraSRBStrategy):
    name = "csrb_24h_2c_p80"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 5.0% of threshold. ROI=+392%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 5.0}


class ContraSRB24h2cP90(ContraSRBStrategy):
    name = "csrb_24h_2c_p90"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 6.8% of threshold. ROI=+389%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 6.8}


class ContraSRB24h2cP95(ContraSRBStrategy):
    name = "csrb_24h_2c_p95"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 8.4% of threshold. ROI=+395%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 8.4}


class ContraSRB24h2cP99(ContraSRBStrategy):
    name = "csrb_24h_2c_p99"
    description = "Contra-SRB: ≤2¢, last 24h, spot within 13.4% of threshold. ROI=+301%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 24.0, "max_distance_pct": 13.4}


# ---------------------------------------------------------------------------
# 12h variants — SOL percentiles: p50=1.7% p60=2.1% p70=2.7% p75=3.0%
#                                  p80=3.5% p90=4.8% p95=6.1% p99=9.8%
# ---------------------------------------------------------------------------

class ContraSRB12h2cP50(ContraSRBStrategy):
    name = "csrb_12h_2c_p50"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 1.7% of threshold. ROI=+433%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 1.7}


class ContraSRB12h2cP60(ContraSRBStrategy):
    name = "csrb_12h_2c_p60"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 2.1% of threshold. ROI=+390%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 2.1}


class ContraSRB12h2cP70(ContraSRBStrategy):
    name = "csrb_12h_2c_p70"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 2.7% of threshold. ROI=+343%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 2.7}


class ContraSRB12h2cP75(ContraSRBStrategy):
    name = "csrb_12h_2c_p75"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 3.0% of threshold. ROI=+343%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 3.0}


class ContraSRB12h2cP80(ContraSRBStrategy):
    name = "csrb_12h_2c_p80"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 3.5% of threshold. ROI=+320%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 3.5}


class ContraSRB12h2cP90(ContraSRBStrategy):
    name = "csrb_12h_2c_p90"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 4.8% of threshold. ROI=+319%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 4.8}


class ContraSRB12h2cP95(ContraSRBStrategy):
    name = "csrb_12h_2c_p95"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 6.1% of threshold. ROI=+327%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 6.1}


class ContraSRB12h2cP99(ContraSRBStrategy):
    name = "csrb_12h_2c_p99"
    description = "Contra-SRB: ≤2¢, last 12h, spot within 9.8% of threshold. ROI=+261%."
    default_params = {**ContraSRBStrategy.default_params,
        "hours_before_res": 12.0, "max_distance_pct": 9.8}


ALL_CONTRA_SRB_CLASSES = [
    # 48h — best window
    ContraSRBStrategy,       # csrb_48h_2c_p90 (+482%) — best overall
    ContraSRB48h2cP80,       # csrb_48h_2c_p80 (+469%)
    ContraSRB48h2cP75,       # csrb_48h_2c_p75 (+449%)
    ContraSRB48h2cP60,       # csrb_48h_2c_p60 (+455%)
    ContraSRB48h2cP50,       # csrb_48h_2c_p50 (+400%)
    ContraSRB48h2cP70,       # csrb_48h_2c_p70 (+436%)
    ContraSRB48h2cP95,       # csrb_48h_2c_p95 (+432%)
    ContraSRB48h2cP99,       # csrb_48h_2c_p99 (+357%)
    # 24h
    ContraSRB24h2cP50,       # csrb_24h_2c_p50 (+429%) — best 24h
    ContraSRB24h2cP75,       # csrb_24h_2c_p75 (+412%)
    ContraSRB24h2cP60,       # csrb_24h_2c_p60 (+400%)
    ContraSRB24h2cP95,       # csrb_24h_2c_p95 (+395%)
    ContraSRB24h2cP70,       # csrb_24h_2c_p70 (+397%)
    ContraSRB24h2cP80,       # csrb_24h_2c_p80 (+392%)
    ContraSRB24h2cP90,       # csrb_24h_2c_p90 (+389%)
    ContraSRB24h2cP99,       # csrb_24h_2c_p99 (+301%)
    # 12h
    ContraSRB12h2cP50,       # csrb_12h_2c_p50 (+433%) — best 12h
    ContraSRB12h2cP60,       # csrb_12h_2c_p60 (+390%)
    ContraSRB12h2cP70,       # csrb_12h_2c_p70 (+343%)
    ContraSRB12h2cP75,       # csrb_12h_2c_p75 (+343%)
    ContraSRB12h2cP80,       # csrb_12h_2c_p80 (+320%)
    ContraSRB12h2cP90,       # csrb_12h_2c_p90 (+319%)
    ContraSRB12h2cP95,       # csrb_12h_2c_p95 (+327%)
    ContraSRB12h2cP99,       # csrb_12h_2c_p99 (+261%)
]
