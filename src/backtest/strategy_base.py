"""
PROPHET STRATEGIES
Strategy base class — ABC for all trading strategies
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List

import pandas as pd

from models.market import Market
from models.trade import BacktestTrade
from backtest.fill_simulator import FillSimulator
from backtest.fee_calculator import FeeCalculator


class StrategyBase(ABC):
    """Abstract base class for all Prophet Strategies."""

    name: str = "base"

    def __init__(self, dm, fill_simulator: FillSimulator, fee_calculator: FeeCalculator):
        self.dm = dm
        self.fill_sim = fill_simulator
        self.fee_calc = fee_calculator

    @abstractmethod
    def generate_trades(
        self,
        market: Market,
        trades_df: pd.DataFrame,
        params: dict,
    ) -> List[BacktestTrade]:
        """
        Given a market and its historical trades, generate simulated BacktestTrades.

        Args:
            market: The Market object (with parsed fields + resolution)
            trades_df: All historical trades for this market
            params: Strategy-specific parameters dict

        Returns:
            List of BacktestTrade objects (filled or unfilled)
        """
        ...

    def _shares_for_capital(self, capital_usd: float, price: float) -> float:
        """How many shares can we buy with capital_usd at given price."""
        if price <= 0:
            return 0.0
        return capital_usd / price

    def _max_multiplier(self, entry_price: float) -> float:
        """Maximum achievable multiplier given entry price (capped by price = 1.0)."""
        if entry_price <= 0:
            return 0.0
        return 1.0 / entry_price
