"""
PROPHET STRATEGIES
Strategy B: Volatility Spread

Concept: Buy YES and NO symmetrically around the current price.
If crypto moves significantly in either direction, one side becomes very valuable.
If price stays flat, both expire worthless.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import List

import pandas as pd

from backtest.strategy_base import StrategyBase
from backtest.fill_simulator import FillSimulator
from backtest.fee_calculator import FeeCalculator
from backtest.strategies.stink_bid import EXIT_MULTIPLIERS
from models.market import Market, Outcome
from models.trade import BacktestTrade

logger = logging.getLogger(__name__)


class VolatilitySpreadStrategy(StrategyBase):
    """
    Volatility Spread — buys YES and NO symmetrically to capture volatility.
    """
    name = "volatility_spread"

    def generate_trades(
        self,
        market: Market,
        trades_df: pd.DataFrame,
        params: dict,
    ) -> List[BacktestTrade]:
        """
        For a given market, simulate symmetric YES + NO buys.
        """
        if not market.is_parsed():
            return []
        if trades_df.empty:
            return []

        entry_price_max = float(params["entry_price_max"])
        capital_per_side = float(params["capital_per_side"])
        exit_strategy = str(params["exit_strategy"])
        sell_target_pct = float(params.get("sell_target_pct", 100))

        # The DataManager cache already has these as datetime objects
        order_time = trades_df["block_time"].min()

        results = []

        for side in ["YES", "NO"]:
            # Use the pre-filtered trades for this outcome
            trades_outcome_df = self.dm.get_trades_for_market(market.condition_id, outcome=side)
            
            trade = self._simulate_one(
                market=market,
                trades_df=trades_outcome_df,
                side=side,
                entry_price_max=entry_price_max,
                capital=capital_per_side,
                exit_strategy=exit_strategy,
                sell_target_pct=sell_target_pct,
                order_time=order_time,
            )
            results.append(trade)

        return results

    def _simulate_one(
        self,
        market: Market,
        trades_df: pd.DataFrame,
        side: str,
        entry_price_max: float,
        capital: float,
        exit_strategy: str,
        sell_target_pct: float,
        order_time: datetime,
    ) -> BacktestTrade:
        """Simulate one side of the volatility spread."""

        shares = self._shares_for_capital(capital, entry_price_max)
        fill_model = self.fill_sim.model

        trade = BacktestTrade(
            condition_id=market.condition_id,
            strategy=self.name,
            crypto=market.crypto.value if market.crypto else "",
            side=side.upper(),
            entry_price=entry_price_max,
            capital=capital,
            shares_bought=shares,
            fill_model=fill_model,
            exit_strategy=exit_strategy,
        )

        fill_result = self.fill_sim.simulate(
            trades_df=trades_df,
            token_outcome=side,
            target_price=entry_price_max,
            order_size_usd=capital,
            order_placed_after=order_time,
        )

        if not fill_result.filled:
            trade.filled = False
            trade.exit_reason = "unfilled"
            trade.gross_pnl = 0.0
            trade.fees_paid = 0.0
            trade.net_pnl = 0.0
            return trade

        trade.filled = True
        trade.fill_time = fill_result.fill_time
        trade.fill_price = fill_result.fill_price
        trade.fill_slippage = fill_result.slippage

        actual_shares = self._shares_for_capital(capital, fill_result.fill_price)
        trade.shares_bought = actual_shares

        # Determine exit
        if exit_strategy == "hold_to_resolution":
            self._apply_resolution_exit(trade, market, actual_shares, capital)

        elif exit_strategy == "sell_at_target":
            multiplier = 1.0 + (sell_target_pct / 100.0)
            self._apply_multiplier_exit(trade, market, trades_df, side, actual_shares, capital, multiplier, exit_strategy)

        elif exit_strategy in EXIT_MULTIPLIERS:
            multiplier = EXIT_MULTIPLIERS[exit_strategy]
            self._apply_multiplier_exit(trade, market, trades_df, side, actual_shares, capital, multiplier, exit_strategy)

        else:
            logger.warning(f"Unknown exit strategy: {exit_strategy}")
            self._apply_resolution_exit(trade, market, actual_shares, capital)

        return trade

    def _apply_multiplier_exit(
        self, trade, market, trades_df, side, actual_shares, capital, multiplier, exit_strategy
    ):
        max_mult = self._max_multiplier(trade.fill_price)
        if multiplier > max_mult:
            self._apply_resolution_exit(trade, market, actual_shares, capital)
            trade.exit_reason += "_fallback_unreachable"
            return

        exit_result = self.fill_sim.find_exit_price_level(
            trades_df=trades_df,
            token_outcome=side,
            entry_price=trade.fill_price,
            target_multiplier=multiplier,
            filled_after=trade.fill_time,
        )

        if exit_result:
            exit_price, exit_time = exit_result
            gross_payout = actual_shares * exit_price
            trade.exit_price = exit_price
            trade.exit_time = exit_time
            trade.exit_reason = exit_strategy
            trade.gross_pnl = gross_payout - capital
            trade.fees_paid = self.fee_calc.trading_fee(capital)
            trade.net_pnl = self.fee_calc.net_pnl(capital, gross_payout)
        else:
            self._apply_resolution_exit(trade, market, actual_shares, capital)
            trade.exit_reason += f"_{exit_strategy}_not_reached"

    def _apply_resolution_exit(self, trade, market, actual_shares, capital):
        trade.exit_price = None
        trade.exit_reason = "resolution"
        trade.resolved_outcome = market.resolved_outcome

        if market.resolved_outcome == Outcome.UNKNOWN:
            trade.gross_pnl = 0.0
            trade.fees_paid = 0.0
            trade.net_pnl = 0.0
            trade.exit_reason = "unresolved_market"
            return

        side_wins = (
            (trade.side == "YES" and market.resolved_outcome == Outcome.YES) or
            (trade.side == "NO" and market.resolved_outcome == Outcome.NO)
        )

        gross_payout = actual_shares * 1.0 if side_wins else 0.0
        trade.exit_price = 1.0 if side_wins else 0.0
        trade.gross_pnl = gross_payout - capital
        trade.fees_paid = self.fee_calc.trading_fee(capital)
        trade.net_pnl = self.fee_calc.net_pnl(capital, gross_payout)
