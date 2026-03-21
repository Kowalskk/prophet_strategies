"""
Abstract base class for all Prophet trading strategies.

Every strategy must subclass :class:`StrategyBase` and implement:
- :meth:`evaluate` — given a market + current data, return trade signals
- :meth:`validate_params` — validate and normalise strategy parameters

New strategies are added by:
1. Creating a new file in ``prophet/strategies/``
2. Subclassing ``StrategyBase``
3. Decorating the class with ``@register_strategy`` from ``registry.py``

No other changes are required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# TradeSignal
# ---------------------------------------------------------------------------


@dataclass
class TradeSignal:
    """A trade recommendation produced by a strategy evaluation.

    Attributes
    ----------
    market_id:
        DB primary key of the :class:`~prophet.db.models.Market`.
    side:
        ``"YES"`` or ``"NO"`` — which outcome token to buy.
    target_price:
        Limit price to place the order at, in [0, 1].
    size_usd:
        Capital to deploy in USD.
    confidence:
        Strategy confidence score in ``[0.0, 1.0]``.
    exit_strategy:
        Named exit rule: ``"hold_to_resolution"``, ``"sell_at_target"``,
        ``"sell_at_Nx"`` (where N is a multiplier).
    exit_params:
        Parameters for the chosen exit strategy.
        For ``"sell_at_target"``: ``{"target_pct": 100.0}`` (sell at 2×).
        For ``"sell_at_Nx"``:    ``{"multiplier": 3.0}``.
        For ``"hold_to_resolution"``: ``{}``.
    metadata:
        Strategy-specific diagnostic data (free-form dict).
    strategy:
        Strategy name that generated this signal (set automatically by
        :meth:`StrategyBase.evaluate` or by the signal generator).
    """

    market_id: int
    side: str  # "YES" | "NO"
    target_price: float
    size_usd: float
    confidence: float  # 0.0 – 1.0
    exit_strategy: str  # "hold_to_resolution" | "sell_at_target" | "sell_at_Nx"
    exit_params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    strategy: str = ""  # filled in by signal generator

    def __post_init__(self) -> None:
        """Normalise and validate signal fields."""
        self.side = self.side.upper()
        if self.side not in ("YES", "NO"):
            raise ValueError(f"TradeSignal.side must be 'YES' or 'NO', got {self.side!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"TradeSignal.confidence must be in [0, 1], got {self.confidence}"
            )
        if not 0.0 < self.target_price <= 1.0:
            raise ValueError(
                f"TradeSignal.target_price must be in (0, 1], got {self.target_price}"
            )
        if self.size_usd <= 0:
            raise ValueError(
                f"TradeSignal.size_usd must be positive, got {self.size_usd}"
            )


# ---------------------------------------------------------------------------
# StrategyBase
# ---------------------------------------------------------------------------


class StrategyBase(ABC):
    """Abstract base class for all trading strategies.

    Class Attributes
    ----------------
    name:
        Unique strategy identifier (used as the registry key).
    description:
        Human-readable description shown in the dashboard.
    default_params:
        Default parameter values.  Overridden per-market via
        ``strategy_configs`` table or the dashboard.
    """

    name: str = ""
    description: str = ""
    default_params: dict[str, Any] = {}

    @abstractmethod
    async def evaluate(
        self,
        market: Any,
        orderbook: dict[str, Any],
        spot_price: float,
        params: dict[str, Any],
    ) -> list[TradeSignal]:
        """Evaluate a market and return zero or more trade signals.

        Called on every scan cycle for each active market assigned to this
        strategy.

        Parameters
        ----------
        market:
            A :class:`~prophet.db.models.Market` ORM instance.
        orderbook:
            Dict with ``"yes"`` and ``"no"`` keys, each holding an
            :class:`~prophet.polymarket.models.OrderBook` instance.
        spot_price:
            Current USD spot price for the market's crypto asset.
        params:
            Strategy parameters (merged: default → crypto-level → market-level
            overrides).  Already validated by :meth:`validate_params`.

        Returns
        -------
        list[TradeSignal]
            Zero or more trade signals.  The caller (SignalGenerator) will
            pass each signal through the RiskManager before persisting.
        """
        ...

    @abstractmethod
    def validate_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalise strategy parameters.

        Should:
        1. Merge ``params`` on top of :attr:`default_params`.
        2. Cast values to the expected types.
        3. Raise ``ValueError`` for invalid combinations.

        Returns
        -------
        dict
            Normalised parameter dict (always a new copy, never mutates input).
        """
        ...

    # ------------------------------------------------------------------
    # Helpers available to all subclasses
    # ------------------------------------------------------------------

    def _merge_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge user-supplied params over the strategy defaults."""
        merged = dict(self.default_params)
        merged.update(params)
        return merged

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
