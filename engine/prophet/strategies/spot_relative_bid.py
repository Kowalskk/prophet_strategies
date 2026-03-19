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

        exit_params = {"target_pct": p["sell_target_pct"]} if p["exit_strategy"] == "sell_at_target" else {}
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


# ── Group 2: Mid tier (5¢ / 1¢) ─────────────────────────────────────────────

class SrbMidRes(SpotRelativeBidStrategy):
    name = "srb_mid_res"
    description = "Spot-Relative Bid: 5¢/1¢ tiers · hold to resolution (~20× / ~100×)"
    default_params = {**SpotRelativeBidStrategy.default_params,
        "tier1_price": 0.05, "tier1_capital": 50.0,
        "tier2_price": 0.01, "tier2_capital": 5.0,
        "exit_strategy": "hold_to_resolution", "sell_target_pct": 100.0}


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


ALL_SRB_CLASSES = [
    SrbCheapRes, SrbCheapX5, SrbCheapX10,
    SrbMidRes, SrbMidX3, SrbMidX5,
    SrbHighRes, SrbHighX2, SrbHighX4,
]
