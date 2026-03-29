"""
Strategy registry — maps strategy names to their implementation classes.

Usage
-----
    from prophet.strategies.registry import get_strategy, list_strategies

    strategy = get_strategy("volatility_spread")
    signals = await strategy.evaluate(market, orderbook, spot_price, params)

Adding a new strategy
---------------------
1. Create a new file in ``prophet/strategies/``.
2. Subclass :class:`~prophet.strategies.base.StrategyBase`.
3. Decorate the class with ``@register_strategy``.

No changes to this file are required.
"""

from __future__ import annotations

import logging
from typing import Any

from prophet.strategies.base import StrategyBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, type[StrategyBase]] = {}


def register_strategy(cls: type[StrategyBase]) -> type[StrategyBase]:
    """Class decorator that registers a strategy in :data:`STRATEGY_REGISTRY`.

    Example::

        @register_strategy
        class MyNewStrategy(StrategyBase):
            name = "my_new_strategy"
            ...
    """
    if not cls.name:
        raise ValueError(
            f"Strategy class {cls.__name__} must define a non-empty `name` attribute."
        )
    if cls.name in STRATEGY_REGISTRY:
        logger.warning(
            "Strategy %r is already registered; overwriting with %s",
            cls.name,
            cls.__name__,
        )
    STRATEGY_REGISTRY[cls.name] = cls
    logger.debug("Registered strategy: %r → %s", cls.name, cls.__name__)
    return cls


def get_strategy(name: str) -> StrategyBase:
    """Return a fresh instance of the named strategy.

    Parameters
    ----------
    name:
        The strategy's ``name`` attribute (e.g. ``"volatility_spread"``).

    Raises
    ------
    KeyError
        If the strategy name is not registered.
    """
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        available = list(STRATEGY_REGISTRY.keys())
        raise KeyError(
            f"Unknown strategy {name!r}. Available: {available}"
        )
    return cls()


def list_strategies() -> list[dict[str, Any]]:
    """Return a list of dicts describing all registered strategies.

    Each dict contains:
    - ``name``          — strategy identifier
    - ``description``   — human-readable description
    - ``default_params``— default parameter values
    """
    return [
        {
            "name": cls.name,
            "description": cls.description,
            "default_params": dict(cls.default_params),
        }
        for cls in STRATEGY_REGISTRY.values()
    ]


# ---------------------------------------------------------------------------
# Register built-in strategies at import time
# ---------------------------------------------------------------------------

# Import side-effects: each module calls @register_strategy on its class.
# Keep this at the bottom of the file to avoid circular imports.

def _register_builtins() -> None:
    """Import built-in strategy modules to trigger their @register_strategy decorators."""
    from prophet.strategies import volatility_spread as _vs  # noqa: F401
    from prophet.strategies import stink_bid as _sb  # noqa: F401
    from prophet.strategies import liquidity_sniper as _ls  # noqa: F401
    from prophet.strategies import volatility_spread_variants as _vsv  # noqa: F401
    from prophet.strategies import spot_relative_bid as _srb  # noqa: F401
    from prophet.strategies import reversal_strategy as _rev  # noqa: F401
    from prophet.strategies import dca_strategy as _dca  # noqa: F401
    from prophet.strategies import ladder_mm_strategy as _lmm  # noqa: F401
    from prophet.strategies import auto_hedge_strategy as _ah  # noqa: F401
    from prophet.strategies import pre_window_strategy as _pw  # noqa: F401

    from prophet.strategies.volatility_spread import VolatilitySpreadStrategy
    from prophet.strategies.stink_bid import StinkBidStrategy
    from prophet.strategies.liquidity_sniper import LiquiditySniperStrategy
    from prophet.strategies.volatility_spread_variants import (
        VolatilitySpreadX3, VolatilitySpreadX4, VolatilitySpreadX5,
    )
    from prophet.strategies.spot_relative_bid import ALL_SRB_CLASSES
    from prophet.strategies.reversal_strategy import (
        ReversalStrategy,
        ReversalAggressiveStrategy,
        ReversalDeepStrategy,
        ReversalScalpStrategy,
    )
    from prophet.strategies.dca_strategy import (
        DCAStrategy,
        DCAConservativeStrategy,
        DCAAggressiveStrategy,
        DCASportsStrategy,
    )
    from prophet.strategies.ladder_mm_strategy import (
        LadderMMStrategy,
        LadderMMWideStrategy,
        LadderMMTightStrategy,
    )
    from prophet.strategies.auto_hedge_strategy import (
        AutoHedgeStrategy,
        AutoHedgeAggressiveStrategy,
        AutoHedgeSniperStrategy,
    )
    from prophet.strategies.pre_window_strategy import (
        PreWindowStrategy,
        PreWindowEarlyStrategy,
        PreWindowLateStrategy,
    )

    for cls in [
        VolatilitySpreadStrategy,
        StinkBidStrategy,
        LiquiditySniperStrategy,
        VolatilitySpreadX3,
        VolatilitySpreadX4,
        VolatilitySpreadX5,
        *ALL_SRB_CLASSES,
        ReversalStrategy,
        ReversalAggressiveStrategy,
        ReversalDeepStrategy,
        ReversalScalpStrategy,
        DCAStrategy,
        DCAConservativeStrategy,
        DCAAggressiveStrategy,
        DCASportsStrategy,
        LadderMMStrategy,
        LadderMMWideStrategy,
        LadderMMTightStrategy,
        AutoHedgeStrategy,
        AutoHedgeAggressiveStrategy,
        AutoHedgeSniperStrategy,
        PreWindowStrategy,
        PreWindowEarlyStrategy,
        PreWindowLateStrategy,
    ]:
        if cls.name not in STRATEGY_REGISTRY:
            STRATEGY_REGISTRY[cls.name] = cls
            logger.debug("Auto-registered built-in strategy: %r", cls.name)
        else:
            logger.debug("Strategy %r already registered", cls.name)


_register_builtins()
