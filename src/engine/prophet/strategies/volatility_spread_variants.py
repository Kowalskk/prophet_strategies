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
    """VS targeting ~3× return — enters when one side ≤33¢, per-side check."""

    name = "vs_x3"
    description = (
        "Volatility Spread targeting ~3× return. "
        "Enters on whichever side has mid price ≤ 0.33. "
        "Exits at ~200% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.97,   # combined fallback (unused when per_side_max set)
        "per_side_max": 0.33,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 200.0,
    }


class VolatilitySpreadX4(VolatilitySpreadStrategy):
    """VS targeting ~4× return — enters when one side ≤25¢, per-side check."""

    name = "vs_x4"
    description = (
        "Volatility Spread targeting ~4× return. "
        "Enters on whichever side has mid price ≤ 0.25. "
        "Exits at ~300% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.97,
        "per_side_max": 0.25,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 300.0,
    }


class VolatilitySpreadX5(VolatilitySpreadStrategy):
    """VS targeting ~5× return — enters when one side ≤20¢, per-side check."""

    name = "vs_x5"
    description = (
        "Volatility Spread targeting ~5× return. "
        "Enters on whichever side has mid price ≤ 0.20. "
        "Exits at ~400% gain or resolution."
    )
    default_params: dict[str, Any] = {
        "spread_percent": 2.0,
        "entry_price_max": 0.97,
        "per_side_max": 0.20,
        "capital_per_side": 10.0,
        "exit_strategy": "sell_at_target",
        "sell_target_pct": 400.0,
    }
