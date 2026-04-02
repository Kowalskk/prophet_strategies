"""
Spot-Relative Bid Strategy — directional stink bids using live spot price.

Unlike the generic stink_bid (which bids both sides on every market),
this strategy uses the current crypto spot price to determine WHICH side
of each market is currently cheap, and only bids that side.

Logic
-----
For a market "ETH above $X by date":
  - If spot_price < threshold → YES is the cheap/unlikely side → buy YES
  - If spot_price > threshold → NO  is the cheap/unlikely side → buy NO
  - If spot_price == threshold (within 1%) → skip (too uncertain)

Two fixed tiers per signal:
  - Tier 1: moderate price, higher capital  (e.g. 3¢ / $50)
  - Tier 2: ultra-cheap price, small capital (e.g. 0.5¢ / $3) — pure lottery

Requires market to have `threshold` and `direction` metadata set by scanner.
Markets without these fields are skipped.
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase, TradeSignal

logger = logging.getLogger(__name__)


class SpotRelativeBidStrategy(StrategyBase):
    """Directional stink bids guided by the live crypto spot price."""

    name = "srb_cheap_res"
    description = (
        "Uses the live spot price to identify which side of each market is cheap. "
        "Places Tier-1 (3¢/$50) and Tier-2 (0.5¢/$3) bids on that side only. "
        "Holds to resolution for maximum payout (~33× and ~200×)."
    )
    default_params: dict[str, Any] = {
        "tier1_price": 0.03,
        "tier1_capital": 50.0,
        "tier2_price": 0.005,
        "tier2_capital": 3.0,
        "exit_strategy": "hold_to_resolution",
        "sell_target_pct": 100.0,   # only used when exit_strategy == sell_at_target
        "min_distance_pct": 1.0,    # skip market if spot within 1% of threshold
        "max_hours_before_res": 0,  # 0 = no filter, >0 = only enter within N hours of resolution
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self.validate_params(params)

        # --- Require threshold metadata ---
        threshold = getattr(market, "threshold", None)
        direction = getattr(market, "direction", None)
        if threshold is None or not threshold or spot_price <= 0:
            logger.debug(
                "%s: market_id=%s missing threshold/spot — skip",
                self.name, market.id,
            )
            return []

        # Normalise direction: we only handle "above" markets (the standard type)
        # direction="ABOVE" means YES resolves if price ends above threshold
        direction = (direction or "ABOVE").upper()
        if direction != "ABOVE":
            logger.debug("%s: market_id=%s direction=%r not supported", self.name, market.id, direction)
            return []

        # --- Determine cheap side ---
        dist_pct = abs(spot_price - threshold) / threshold * 100.0
        if dist_pct < p["min_distance_pct"]:
            logger.debug(
                "%s: market_id=%s spot=%.2f threshold=%.2f dist=%.2f%% < min %.2f%% — skip",
                self.name, market.id, spot_price, threshold, dist_pct, p["min_distance_pct"],
            )
            return []

        # --- Time window filter ---
        max_hours = float(p.get("max_hours_before_res", 0))
        if max_hours > 0:
            resolution_time = getattr(market, "resolution_time", None)
            if resolution_time is None:
                resolution_date = getattr(market, "resolution_date", None)
                if resolution_date is None:
                    return []
                if isinstance(resolution_date, str):
                    from datetime import date as _date
                    resolution_date = _date.fromisoformat(resolution_date)
                from datetime import datetime as _dt, timezone as _tz
                resolution_time = _dt(
                    resolution_date.year, resolution_date.month, resolution_date.day,
                    23, 59, 59, tzinfo=_tz.utc,
                )
            else:
                from datetime import timezone as _tz
                if resolution_time.tzinfo is None:
                    resolution_time = resolution_time.replace(tzinfo=_tz.utc)
            from datetime import datetime as _dt, timezone as _tz
            hours_remaining = (resolution_time - _dt.now(_tz.utc)).total_seconds() / 3600.0
            if hours_remaining <= 0 or hours_remaining > max_hours:
                return []

        if spot_price < threshold:
            # ETH below threshold → "above" is unlikely → YES is cheap
            cheap_side = "YES"
        else:
            # ETH above threshold → "above" is likely → NO is cheap
            cheap_side = "NO"

        # --- Check orderbook for that side ---
        book = orderbook.get(cheap_side.lower())
        if book is None:
            logger.debug("%s: market_id=%s no %s orderbook", self.name, market.id, cheap_side)
            return []

        best_ask = getattr(book, "best_ask", None)
        if best_ask is None:
            asks = getattr(book, "asks", None)
            if asks:
                best_ask = float(asks[0].price)

        if best_ask is None:
            logger.debug("%s: market_id=%s no best_ask for %s", self.name, market.id, cheap_side)
            return []

        if p["exit_strategy"] in ("sell_at_target", "time_exit"):
            exit_params: dict[str, Any] = {"target_pct": p["sell_target_pct"]}
            if p["exit_strategy"] == "time_exit":
                exit_params["days_before_expiry"] = p.get("days_before_expiry", 3.0)
        else:
            exit_params = {}
        signals: list[TradeSignal] = []

        # Tier 1
        if best_ask > p["tier1_price"]:
            signals.append(TradeSignal(
                market_id=market.id,
                side=cheap_side,
                target_price=p["tier1_price"],
                size_usd=p["tier1_capital"],
                confidence=0.55,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata={
                    "tier": "tier1",
                    "spot_price": spot_price,
                    "threshold": threshold,
                    "distance_pct": round(dist_pct, 2),
                    "best_ask": best_ask,
                    "potential_multiplier": round(1.0 / p["tier1_price"], 1),
                },
                strategy=self.name,
            ))

        # Tier 2
        if best_ask > p["tier2_price"]:
            signals.append(TradeSignal(
                market_id=market.id,
                side=cheap_side,
                target_price=p["tier2_price"],
                size_usd=p["tier2_capital"],
                confidence=0.45,
                exit_strategy=p["exit_strategy"],
                exit_params=exit_params,
                metadata={
                    "tier": "tier2",
                    "spot_price": spot_price,
                    "threshold": threshold,
                    "distance_pct": round(dist_pct, 2),
                    "best_ask": best_ask,
                    "potential_multiplier": round(1.0 / p["tier2_price"], 1),
                },
                strategy=self.name,
            ))

        if signals:
            logger.info(
                "%s: market_id=%d %s@%.4f dist=%.1f%% → %d signal(s)",
                self.name, market.id, cheap_side, p["tier1_price"], dist_pct, len(signals),
            )

        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        p["tier1_price"] = float(p["tier1_price"])
        p["tier2_price"] = float(p["tier2_price"])
        p["tier1_capital"] = float(p["tier1_capital"])
        p["tier2_capital"] = float(p["tier2_capital"])
        p["sell_target_pct"] = float(p["sell_target_pct"])
        p["min_distance_pct"] = float(p["min_distance_pct"])
        return p


# ---------------------------------------------------------------------------
# 9 variants — 3 tier groups × 3 exit strategies
# ---------------------------------------------------------------------------

# ── Group 1: Ultra cheap (3¢ / 0.5¢) ───────────────────────────────────────

class SrbCheapRes(SpotRelativeBidStrategy):
    name = "srb_cheap_res"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · hold to resolution (~33× / ~200×)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "hold_to_resolution", "sell_target_pct": 100.0}


class SrbCheapX5(SpotRelativeBidStrategy):
    name = "srb_cheap_x5"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 5× (400% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 400.0}


class SrbCheapX10(SpotRelativeBidStrategy):
    name = "srb_cheap_x10"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 10× (900% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 900.0}


# ── Group 1.x: Cheap tier scalp variants ────────────────────────────────────

class SrbCheapX1p3(SpotRelativeBidStrategy):
    name = "srb_cheap_x1p3"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at +30% — scalp"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 30.0}


class SrbCheapX1p5(SpotRelativeBidStrategy):
    name = "srb_cheap_x1p5"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at +50% — fast rotation"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 50.0}


# ── Group 2: Mid tier (5¢ / 1¢) ─────────────────────────────────────────────

class SrbMidRes(SpotRelativeBidStrategy):
    name = "srb_mid_res"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · hold to resolution (~20× / ~100×)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "hold_to_resolution", "sell_target_pct": 100.0}


class SrbMidX1p3(SpotRelativeBidStrategy):
    name = "srb_mid_x1p3"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at +30% — scalp"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 30.0}


class SrbMidX1p5(SpotRelativeBidStrategy):
    name = "srb_mid_x1p5"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at +50% — fast rotation"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 50.0}


class SrbMidX3(SpotRelativeBidStrategy):
    name = "srb_mid_x3"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 3× (200% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 200.0}


class SrbMidX5(SpotRelativeBidStrategy):
    name = "srb_mid_x5"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 5× (400% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 400.0}


# ── Group 3: High tier (8¢ / 2¢) — more fills, lower multiplier ─────────────

class SrbHighRes(SpotRelativeBidStrategy):
    name = "srb_high_res"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · hold to resolution (~12× / ~50×)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "hold_to_resolution", "sell_target_pct": 100.0}


class SrbHighX1p3(SpotRelativeBidStrategy):
    name = "srb_high_x1p3"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at +30% — scalp"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 30.0}


class SrbHighX1p5(SpotRelativeBidStrategy):
    name = "srb_high_x1p5"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at +50% — fast rotation"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 50.0}


class SrbHighX2(SpotRelativeBidStrategy):
    name = "srb_high_x2"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at 2× (100% gain) — fast rotation"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 100.0}


class SrbHighX4(SpotRelativeBidStrategy):
    name = "srb_high_x4"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at 4× (300% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 300.0}


class GenericSRBStrategy(StrategyBase):
    """SRB for non-crypto markets (sports, politics, etc.) — no spot price needed.

    Uses current order book prices to find cheap sides. Applies NO-bias at low
    prices based on Becker 2026 research: NO@<10¢ has +23% EV vs YES -41% EV.
    """

    name = "srb_generic"
    description = (
        "Generic SRB for non-crypto markets. Bids on cheap sides with NO-bias "
        "at low prices (research: NO@<10¢ = +23% EV, YES@<10¢ = -41% EV)."
    )
    default_params: dict[str, Any] = {
        "max_price": 0.10,          # only bid on sides priced below this
        "tier1_price": 0.04,        # bid price for tier 1
        "tier1_capital": 30.0,
        "tier2_price": 0.01,        # bid price for tier 2 (ultra cheap)
        "tier2_capital": 5.0,
        "no_bias": True,            # prefer NO side at low prices (Becker 2026)
        "no_bias_threshold": 0.10,  # apply NO-bias when price < this
        "exit_strategy": "hold_to_resolution",
        "sell_target_pct": 100.0,
    }

    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        p = self.validate_params(params)

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
            if best_ask is None or best_ask > p["max_price"]:
                continue

            # NO-bias: at low prices, skip YES side (Becker 2026: YES@<10¢ = -41% EV)
            if p["no_bias"] and best_ask < p["no_bias_threshold"] and side_name == "yes":
                logger.debug(
                    "%s: market_id=%s skipping YES@%.3f (NO-bias active)",
                    self.name, market.id, best_ask,
                )
                continue

            side = side_name.upper()
            exit_params = {"target_pct": p["sell_target_pct"]} if p["exit_strategy"] == "sell_at_target" else {}

            # Tier 1
            if best_ask > p["tier1_price"]:
                signals.append(TradeSignal(
                    market_id=market.id,
                    side=side,
                    target_price=p["tier1_price"],
                    size_usd=p["tier1_capital"],
                    confidence=0.50,
                    exit_strategy=p["exit_strategy"],
                    exit_params=exit_params,
                    metadata={
                        "tier": "tier1",
                        "best_ask": best_ask,
                        "no_bias": p["no_bias"],
                        "potential_multiplier": round(1.0 / p["tier1_price"], 1),
                    },
                    strategy=self.name,
                ))

            # Tier 2
            if best_ask > p["tier2_price"]:
                signals.append(TradeSignal(
                    market_id=market.id,
                    side=side,
                    target_price=p["tier2_price"],
                    size_usd=p["tier2_capital"],
                    confidence=0.40,
                    exit_strategy=p["exit_strategy"],
                    exit_params=exit_params,
                    metadata={
                        "tier": "tier2",
                        "best_ask": best_ask,
                        "no_bias": p["no_bias"],
                        "potential_multiplier": round(1.0 / p["tier2_price"], 1),
                    },
                    strategy=self.name,
                ))

        if signals:
            logger.info(
                "%s: market_id=%d → %d signal(s)", self.name, market.id, len(signals),
            )
        return signals

    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        p = self._merge_params(params)
        for k in ("max_price", "tier1_price", "tier2_price", "tier1_capital",
                   "tier2_capital", "no_bias_threshold", "sell_target_pct"):
            p[k] = float(p[k])
        p["no_bias"] = bool(p["no_bias"])
        return p


class GenericSrbRes(GenericSRBStrategy):
    name = "srb_generic_res"
    description = "Generic SRB: 4¢/1¢ tiers · hold to resolution · NO-bias"


class GenericSrbX1p5(GenericSRBStrategy):
    name = "srb_generic_x1p5"
    description = "Generic SRB: 4¢/1¢ tiers · sell at +50% — scalp · NO-bias"
    default_params = {**GenericSRBStrategy.default_params,
        "exit_strategy": "sell_at_target", "sell_target_pct": 50.0}


class GenericSrbX2(GenericSRBStrategy):
    name = "srb_generic_x2"
    description = "Generic SRB: 4¢/1¢ tiers · sell at 2× · NO-bias"
    default_params = {**GenericSRBStrategy.default_params,
        "exit_strategy": "sell_at_target", "sell_target_pct": 100.0}


class GenericSrbX5(GenericSRBStrategy):
    name = "srb_generic_x5"
    description = "Generic SRB: 4¢/1¢ tiers · sell at 5× · NO-bias"
    default_params = {**GenericSRBStrategy.default_params,
        "exit_strategy": "sell_at_target", "sell_target_pct": 400.0}


class GenericSrbX10(GenericSRBStrategy):
    name = "srb_generic_x10"
    description = "Generic SRB: 4¢/1¢ tiers · sell at 10× · NO-bias"
    default_params = {**GenericSRBStrategy.default_params,
        "exit_strategy": "sell_at_target", "sell_target_pct": 900.0}


ALL_GENERIC_SRB_CLASSES = [GenericSrbRes, GenericSrbX1p5, GenericSrbX2, GenericSrbX5, GenericSrbX10]


# ── Group 4: Fat tier (15¢ / 25¢) — liquid markets, moderate multiplier ─────

class SrbFatX2(SpotRelativeBidStrategy):
    name = "srb_fat_x2"
    description = "Spot-Relative Bid: 25¢/15¢ tiers · sell at 2× (100% gain) — liquid markets"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.25, "tier1_capital": 20.0,
        "tier2_price": 0.15, "tier2_capital": 10.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 100.0,
        "min_distance_pct": 5.0}   # requires at least 5% distance — avoid near-50/50


class SrbFatX50(SpotRelativeBidStrategy):
    name = "srb_fat_x50"
    description = "Spot-Relative Bid: 25¢/15¢ tiers · sell at +50% — quick rotation"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.25, "tier1_capital": 20.0,
        "tier2_price": 0.15, "tier2_capital": 10.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 50.0,
        "min_distance_pct": 5.0}


# ── Group 5: Time-exit variants (temporal decay) ─────────────────────────────
# Same entries as cheap/mid/high but exit N days before resolution at market
# price — avoids total resolution loss, accepts partial loss instead.

class SrbCheapX3(SpotRelativeBidStrategy):
    name = "srb_cheap_x3"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 3× (200% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 200.0}


class SrbCheapX4(SpotRelativeBidStrategy):
    name = "srb_cheap_x4"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 4× (300% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 300.0}


class SrbMidX4(SpotRelativeBidStrategy):
    name = "srb_mid_x4"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 4× (300% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 300.0}


class SrbMidX10(SpotRelativeBidStrategy):
    name = "srb_mid_x10"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 10× (900% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 900.0}


class SrbHighX3(SpotRelativeBidStrategy):
    name = "srb_high_x3"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at 3× (200% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 200.0}


class SrbHighX5(SpotRelativeBidStrategy):
    name = "srb_high_x5"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at 5× (400% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 400.0}


class SrbHighX10(SpotRelativeBidStrategy):
    name = "srb_high_x10"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · sell at 10× (900% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 900.0}


class SrbFatRes(SpotRelativeBidStrategy):
    name = "srb_fat_res"
    description = "Spot-Relative Bid: 25¢/15¢ tiers · hold to resolution"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.25, "tier1_capital": 20.0,
        "tier2_price": 0.15, "tier2_capital": 10.0,
        "exit_strategy": "hold_to_resolution", "sell_target_pct": 100.0,
        "min_distance_pct": 5.0}


class SrbFatX3(SpotRelativeBidStrategy):
    name = "srb_fat_x3"
    description = "Spot-Relative Bid: 25¢/15¢ tiers · sell at 3× (200% gain)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.25, "tier1_capital": 20.0,
        "tier2_price": 0.15, "tier2_capital": 10.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 200.0,
        "min_distance_pct": 5.0}


class SrbFatX30(SpotRelativeBidStrategy):
    name = "srb_fat_x30"
    description = "Spot-Relative Bid: 25¢/15¢ tiers · sell at +30% — scalp"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.25, "tier1_capital": 20.0,
        "tier2_price": 0.15, "tier2_capital": 10.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 30.0,
        "min_distance_pct": 5.0}


class SrbCheapTE(SpotRelativeBidStrategy):
    name = "srb_cheap_te"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · time_exit 3d before expiry · target 5×"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "time_exit", "sell_target_pct": 400.0,
        "days_before_expiry": 3.0}


class SrbMidTE(SpotRelativeBidStrategy):
    name = "srb_mid_te"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · time_exit 3d before expiry · target 3×"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "time_exit", "sell_target_pct": 200.0,
        "days_before_expiry": 3.0}


class SrbHighTE(SpotRelativeBidStrategy):
    name = "srb_high_te"
    description = "Spot-Relative Bid: 8¢/2¢ tiers · time_exit 3d before expiry · target 2×"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.08, "tier1_capital": 30.0,
        "tier2_price": 0.02, "tier2_capital": 5.0,
        "exit_strategy": "time_exit", "sell_target_pct": 100.0,
        "days_before_expiry": 3.0}


# ── Sweet spots: Cheap tier x6–x30 (backtest 2026-04-01) ────────────────────

class SrbCheapX6(SpotRelativeBidStrategy):
    name = "srb_cheap_x6"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 6× (+500%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 500.0}


class SrbCheapX7(SpotRelativeBidStrategy):
    name = "srb_cheap_x7"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 7× (+600%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 600.0}


class SrbCheapX8(SpotRelativeBidStrategy):
    name = "srb_cheap_x8"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 8× (+700%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 700.0}


class SrbCheapX9(SpotRelativeBidStrategy):
    name = "srb_cheap_x9"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 9× (+800%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 800.0}


class SrbCheapX12(SpotRelativeBidStrategy):
    name = "srb_cheap_x12"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 12× (+1100%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1100.0}


class SrbCheapX15(SpotRelativeBidStrategy):
    name = "srb_cheap_x15"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 15× (+1400%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1400.0}


class SrbCheapX20(SpotRelativeBidStrategy):
    name = "srb_cheap_x20"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 20× (+1900%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1900.0}


class SrbCheapX25(SpotRelativeBidStrategy):
    name = "srb_cheap_x25"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 25× (+2400%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 2400.0}


class SrbCheapX30(SpotRelativeBidStrategy):
    name = "srb_cheap_x30"
    description = "Spot-Relative Bid: 3¢/0.5¢ tiers · sell at 30× (+2900%) — best backtest ROI"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.03, "tier1_capital": 50.0,
        "tier2_price": 0.005, "tier2_capital": 3.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 2900.0}


# ── Sweet spots: Mid tier x6–x18 (backtest 2026-04-01) ──────────────────────

class SrbMidX6(SpotRelativeBidStrategy):
    name = "srb_mid_x6"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 6× (+500%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 500.0}


class SrbMidX7(SpotRelativeBidStrategy):
    name = "srb_mid_x7"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 7× (+600%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 600.0}


class SrbMidX8(SpotRelativeBidStrategy):
    name = "srb_mid_x8"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 8× (+700%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 700.0}


class SrbMidX9(SpotRelativeBidStrategy):
    name = "srb_mid_x9"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 9× (+800%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 800.0}


class SrbMidX12(SpotRelativeBidStrategy):
    name = "srb_mid_x12"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 12× (+1100%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1100.0}


class SrbMidX15(SpotRelativeBidStrategy):
    name = "srb_mid_x15"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 15× (+1400%)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1400.0}


class SrbMidX18(SpotRelativeBidStrategy):
    name = "srb_mid_x18"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · sell at 18× (+1700%) — best mid backtest ROI"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "sell_at_target", "sell_target_pct": 1700.0}


# ---------------------------------------------------------------------------
# Time-windowed variants (12h / 24h / 48h before resolution)
# ---------------------------------------------------------------------------

# Helper to build time-windowed classes dynamically
def _make_timed_class(base_cls, hours, suffix):
    name = f"{base_cls.name}_{suffix}"
    desc = f"{base_cls.description} [only last {hours}h before resolution]"
    params = {**base_cls.default_params, "max_hours_before_res": float(hours)}
    return type(
        f"{base_cls.__name__}_{suffix.upper()}",
        (base_cls,),
        {"name": name, "description": desc, "default_params": params},
    )

# Strategies worth time-filtering (confirmed by backtest)
_TIMED_BASES = [
    SrbCheapRes, SrbCheapX5, SrbCheapX10,
    SrbMidRes, SrbMidX5, SrbMidX10,
]
_TIMED_WINDOWS = [(4, "4h"), (6, "6h"), (12, "12h"), (24, "24h"), (48, "48h")]

ALL_TIMED_SRB_CLASSES = []
for _base in _TIMED_BASES:
    for _hours, _suffix in _TIMED_WINDOWS:
        ALL_TIMED_SRB_CLASSES.append(_make_timed_class(_base, _hours, _suffix))


ALL_SRB_CLASSES = [
    # cheap
    SrbCheapRes, SrbCheapX1p3, SrbCheapX1p5, SrbCheapX3, SrbCheapX4, SrbCheapX5,
    SrbCheapX6, SrbCheapX7, SrbCheapX8, SrbCheapX9, SrbCheapX10,
    SrbCheapX12, SrbCheapX15, SrbCheapX20, SrbCheapX25, SrbCheapX30,
    SrbCheapTE,
    # mid
    SrbMidRes, SrbMidX1p3, SrbMidX1p5, SrbMidX3, SrbMidX4, SrbMidX5,
    SrbMidX6, SrbMidX7, SrbMidX8, SrbMidX9, SrbMidX10,
    SrbMidX12, SrbMidX15, SrbMidX18,
    SrbMidTE,
    # high
    SrbHighRes, SrbHighX1p3, SrbHighX1p5, SrbHighX2, SrbHighX3, SrbHighX4, SrbHighX5, SrbHighX10, SrbHighTE,
    # fat
    SrbFatRes, SrbFatX2, SrbFatX3, SrbFatX30, SrbFatX50,
    *ALL_GENERIC_SRB_CLASSES,
    # time-windowed
    *ALL_TIMED_SRB_CLASSES,
]
