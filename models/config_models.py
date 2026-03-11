"""
PROPHET STRATEGIES
Typed configuration dataclasses
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StinkBidConfig:
    tier1_price: float
    tier1_capital: float
    tier2_price: float
    tier2_capital: float
    exit_strategy: str          # "hold_to_resolution" | "sell_at_Nx"
    crypto: str

    # Valid exit strategies
    VALID_EXITS = {
        "hold_to_resolution",
        "sell_at_2x", "sell_at_5x", "sell_at_10x",
        "sell_at_15x", "sell_at_25x", "sell_at_50x",
        "sell_at_75x", "sell_at_100x", "sell_at_125x", "sell_at_150x",
    }

    def exit_multiplier(self) -> float | None:
        """Return the numeric multiplier, or None if hold_to_resolution."""
        if self.exit_strategy == "hold_to_resolution":
            return None
        try:
            return float(self.exit_strategy.replace("sell_at_", "").replace("x", ""))
        except ValueError:
            return None

    def to_dict(self) -> dict:
        return {
            "strategy": "stink_bid",
            "tier1_price": self.tier1_price,
            "tier1_capital": self.tier1_capital,
            "tier2_price": self.tier2_price,
            "tier2_capital": self.tier2_capital,
            "exit_strategy": self.exit_strategy,
            "crypto": self.crypto,
        }


@dataclass
class VolatilitySpreadConfig:
    spread_percent: float
    entry_price_max: float
    capital_per_side: float
    exit_strategy: str
    sell_target_pct: float
    crypto: str

    VALID_EXITS = {
        "hold_to_resolution", "sell_at_target",
        "sell_at_2x", "sell_at_5x", "sell_at_10x",
        "sell_at_15x", "sell_at_25x", "sell_at_50x",
        "sell_at_75x", "sell_at_100x", "sell_at_125x", "sell_at_150x",
    }

    def exit_multiplier(self) -> float | None:
        if self.exit_strategy == "hold_to_resolution":
            return None
        if self.exit_strategy == "sell_at_target":
            return 1.0 + self.sell_target_pct / 100.0
        try:
            return float(self.exit_strategy.replace("sell_at_", "").replace("x", ""))
        except ValueError:
            return None

    def to_dict(self) -> dict:
        return {
            "strategy": "volatility_spread",
            "spread_percent": self.spread_percent,
            "entry_price_max": self.entry_price_max,
            "capital_per_side": self.capital_per_side,
            "exit_strategy": self.exit_strategy,
            "sell_target_pct": self.sell_target_pct,
            "crypto": self.crypto,
        }


@dataclass
class FillSimConfig:
    model: str                  # "optimistic" | "realistic"
    queue_multiplier: float = 3.0
    min_volume_at_level: float = 10.0
    slippage_bps: float = 50.0


@dataclass
class FeeConfig:
    trading_fee_pct: float = 2.0
    resolution_fee_pct: float = 0.0
