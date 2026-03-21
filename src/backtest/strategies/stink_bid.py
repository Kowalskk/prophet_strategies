"""
PROPHET STRATEGIES
Strategy A: Stink Bid Extremo

Concept: Place very cheap limit orders on extreme price levels.
If the market moves sharply, one order fills and the gain is massive.

For each resolved market, we simulate:
  - Tier 1: Buy YES (if market is "above X") at tier1_price (e.g. 3¢)
             Buy NO  (if market is "above X") at tier1_price (e.g. 3¢)
  - Tier 2: Same but at tier2_price (e.g. 0.2¢) — lottery ticket level

Exit strategies:
  - hold_to_resolution: Wait for market to resolve (YES=1.0, NO=1.0)
  - sell_at_Nx: Sell when price reaches N * entry_price

Supported multipliers: 2x, 5x, 10x, 15x, 25x, 50x, 75x, 100x, 125x, 150x
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd

from backtest.strategy_base import StrategyBase
from backtest.fill_simulator import FillSimulator
from backtest.fee_calculator import FeeCalculator
from models.market import Market, Outcome
from models.trade import BacktestTrade

logger = logging.getLogger(__name__)

# All supported exit multipliers
EXIT_MULTIPLIERS = {
    "sell_at_2x": 2.0,
    "sell_at_5x": 5.0,
    "sell_at_10x": 10.0,
    "sell_at_15x": 15.0,
    "sell_at_25x": 25.0,
    "sell_at_50x": 50.0,
    "sell_at_75x": 75.0,
    "sell_at_100x": 100.0,
    "sell_at_125x": 125.0,
    "sell_at_150x": 150.0,
}


class StinkBidStrategy(StrategyBase):
    """
    Stink Bid Extremo — buys YES and NO tokens at extreme discount prices.
    """
    name = "stink_bid"

    def generate_trades(
        self,
        market: Market,
        trades_df: pd.DataFrame,
        params: dict,
    ) -> List[BacktestTrade]:
        """
        For a given market, simulate 4 potential trades:
        - YES tier1, YES tier2, NO tier1, NO tier2

        Only markets with a valid resolution are useful for P&L calculation.
        """
        if not market.is_parsed():
            return []

        tier1_price = float(params["tier1_price"])
        tier2_price = float(params["tier2_price"])
        tier1_capital = float(params["tier1_capital"])
        tier2_capital = float(params["tier2_capital"])
        exit_strategy = str(params["exit_strategy"])

        # Determine the order placement time:
        # We place orders at market open (first available trade time)
        if trades_df.empty:
            return []

        # The DataManager cache already has these as datetime objects
        order_time = trades_df["block_time"].min()

        results = []

        # Simulate both YES and NO stink bids at both tiers
        for side in ["YES", "NO"]:
            for tier, price, capital in [
                ("tier1", tier1_price, tier1_capital),
                ("tier2", tier2_price, tier2_capital),
            ]:
                # Use the pre-filtered trades for this outcome
                trades_outcome_df = self.dm.get_trades_for_market(market.condition_id, outcome=side)
                
                trade = self._simulate_one(
                    market=market,
                    trades_df=trades_outcome_df,
                    side=side,
                    target_price=price,
                    capital=capital,
                    exit_strategy=exit_strategy,
                    order_time=order_time,
                    tier=tier,
                )
                results.append(trade)

        return results

    def _simulate_one(
        self,
        market: Market,
        trades_df: pd.DataFrame,
        side: str,
        target_price: float,
        capital: float,
        exit_strategy: str,
        order_time: datetime,
        tier: str,
    ) -> BacktestTrade:
        """Simulate a single stink bid order."""

        shares = self._shares_for_capital(capital, target_price)
        fill_model = self.fill_sim.model

        trade = BacktestTrade(
            condition_id=market.condition_id,
            strategy=self.name,
            crypto=market.crypto.value if market.crypto else "",
            side=side.upper(),
            entry_price=target_price,
            capital=capital,
            shares_bought=shares,
            fill_model=fill_model,
            exit_strategy=exit_strategy,
        )

        # --- Step 1: Check if order would have been filled ---
        fill_result = self.fill_sim.simulate(
            trades_df=trades_df,
            token_outcome=side,
            target_price=target_price,
            order_size_usd=capital,
            order_placed_after=order_time,
        )

        if not fill_result.filled:
            # Order never filled — no capital at risk, P&L = 0
            trade.filled = False
            trade.exit_reason = "unfilled"
            trade.gross_pnl = 0.0
            trade.fees_paid = 0.0
            trade.net_pnl = 0.0
            return trade

        # Order was filled
        trade.filled = True
        trade.fill_time = fill_result.fill_time
        trade.fill_price = fill_result.fill_price
        trade.fill_slippage = fill_result.slippage

        # Recalculate shares with actual fill price
        actual_shares = self._shares_for_capital(capital, fill_result.fill_price)
        trade.shares_bought = actual_shares

        # --- Step 2: Determine exit ---
        if exit_strategy == "hold_to_resolution":
            self._apply_resolution_exit(trade, market, actual_shares, capital)

        elif exit_strategy in EXIT_MULTIPLIERS:
            multiplier = EXIT_MULTIPLIERS[exit_strategy]
            max_mult = self._max_multiplier(fill_result.fill_price)

            if multiplier > max_mult:
                # This multiplier is unreachable (price capped at 1.0)
                # Fall back to hold_to_resolution
                logger.debug(
                    f"{exit_strategy} unreachable for entry={fill_result.fill_price:.4f} "
                    f"(max={max_mult:.1f}x). Falling back to resolution."
                )
                self._apply_resolution_exit(trade, market, actual_shares, capital)
                trade.exit_reason += "_fallback_from_unreachable_multiplier"
            else:
                # Search for exit price in historical trades
                exit_result = self.fill_sim.find_exit_price_level(
                    trades_df=trades_df,
                    token_outcome=side,
                    entry_price=fill_result.fill_price,
                    target_multiplier=multiplier,
                    filled_after=fill_result.fill_time,
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
                    # Target never reached — hold to resolution
                    self._apply_resolution_exit(trade, market, actual_shares, capital)
                    trade.exit_reason += f"_{exit_strategy}_target_not_reached"

        else:
            logger.warning(f"Unknown exit strategy: {exit_strategy}")
            self._apply_resolution_exit(trade, market, actual_shares, capital)

        return trade

    def _apply_resolution_exit(
        self,
        trade: BacktestTrade,
        market: Market,
        actual_shares: float,
        capital: float,
    ):
        """Apply P&L based on market resolution outcome."""
        trade.exit_price = None
        trade.exit_reason = "resolution"
        trade.resolved_outcome = market.resolved_outcome

        if market.resolved_outcome == Outcome.UNKNOWN:
            # Market not resolved yet — can't calculate P&L
            trade.gross_pnl = 0.0
            trade.fees_paid = 0.0
            trade.net_pnl = 0.0
            trade.exit_reason = "unresolved_market"
            return

        # Does our side win?
        side_wins = (
            (trade.side == "YES" and market.resolved_outcome == Outcome.YES) or
            (trade.side == "NO" and market.resolved_outcome == Outcome.NO)
        )

        if side_wins:
            # Each share pays out $1.00
            gross_payout = actual_shares * 1.0
            trade.exit_price = 1.0
        else:
            # Each share pays $0.00
            gross_payout = 0.0
            trade.exit_price = 0.0

        trade.gross_pnl = gross_payout - capital
        trade.fees_paid = self.fee_calc.trading_fee(capital)
        trade.net_pnl = self.fee_calc.net_pnl(capital, gross_payout)
