"""
Volatility Spread variants — X3, X4, X5 return targets.

Each variant sets a maximum entry price that implies a specific return multiple
when the position resolves at $1.00:

  VS-X3  entry ≤ 0.33¢  →  ~3× return at resolution
  VS-X4  entry ≤ 0.25¢  →  ~4× return at resolution
  VS-X5  entry ≤ 0.20¢  →  ~5× return at resolution

These are subclasses of VolatilitySpreadStrategy with hardcoded default_params.
All logic (entry calculation, signal emission) is inherited unchanged.
"""

from __future__ import annotations

from typing import Any

from prophet.strategies.volatility_spread import VolatilitySpreadStrategy


class VolatilitySpreadX3(VolatilitySpreadStrategy):
    """VS targeting ~3× return — enters at ≤33¢, holds to resolution."""

    name = "vs_x3"
    description = (
        "Volatility Spread targeting ~3× return. "
        "Places YES and NO limit orders only when mid price is at or below 0.33. "
        "Holds position until ~200% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.33,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 200.0,
    }


class VolatilitySpreadX4(VolatilitySpreadStrategy):
    """VS targeting ~4× return — enters at ≤25¢, holds to resolution."""

    name = "vs_x4"
    description = (
        "Volatility Spread targeting ~4× return. "
        "Places YES and NO limit orders only when mid price is at or below 0.25. "
        "Holds position until ~300% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.25,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 300.0,
    }


class VolatilitySpreadX5(VolatilitySpreadStrategy):
    """VS targeting ~5× return — enters at ≤20¢, holds to resolution."""

    name = "vs_x5"
    description = (
        "Volatility Spread targeting ~5× return. "
        "Places YES and NO limit orders only when mid price is at or below 0.20. "
        "Holds position until ~400% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.20,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 400.0,
    }
