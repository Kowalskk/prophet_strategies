"""
PROPHET STRATEGIES
Backtest Engine — runs a single backtest configuration across all markets
"""
from __future__ import annotations
import logging
import time
from dataclasses import asdict
from typing import List, Optional

import numpy as np
import pandas as pd

from backtest.fee_calculator import FeeCalculator
from backtest.fill_simulator import FillSimulator
from backtest.strategies.stink_bid import StinkBidStrategy
from backtest.strategies.volatility_spread import VolatilitySpreadStrategy
from models.market import Market, Outcome
from models.trade import BacktestTrade, BacktestResult

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Executes a backtest for a given strategy + params across all markets.
    Uses a DataManager to load market and trade data.
    """

    def __init__(self, data_manager, fill_model: str = "realistic"):
        self.dm = data_manager
        self.fill_model_name = fill_model

        # Fee calculator (shared)
        self.fee_calc = FeeCalculator(
            trading_fee_pct=2.0,
            resolution_fee_pct=0.0,
        )

    def _make_fill_sim(self) -> FillSimulator:
        if self.fill_model_name == "optimistic":
            return FillSimulator(model="optimistic")
        return FillSimulator(
            model="realistic",
            queue_multiplier=3.0,
            min_volume_at_level=10.0,
            slippage_bps=50.0,
        )

    def run(
        self,
        strategy_name: str,
        params: dict,
        crypto: Optional[str] = None,
    ) -> BacktestResult:
        """
        Run a full backtest for the given strategy and params.

        Args:
            strategy_name: "stink_bid" or "volatility_spread"
            params: Strategy parameter dict
            crypto: Filter to specific crypto (BTC/ETH/SOL), or None for all

        Returns:
            BacktestResult with all metrics computed
        """
        start_time = time.time()

        self.fill_sim = self._make_fill_sim()

        if strategy_name == "stink_bid":
            from backtest.strategies.stink_bid import StinkBidStrategy
            self.strategy = StinkBidStrategy(self.dm, self.fill_sim, self.fee_calc)
        elif strategy_name == "volatility_spread":
            from backtest.strategies.volatility_spread import VolatilitySpreadStrategy
            self.strategy = VolatilitySpreadStrategy(self.dm, self.fill_sim, self.fee_calc)
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        # Load markets
        if crypto in self.dm.market_cache:
            markets = self.dm.market_cache[crypto]
        else:
            markets_df = self.dm.get_markets(crypto=crypto, resolved_only=True, min_trades=5)
            markets = [self._row_to_market(mrow) for _, mrow in markets_df.iterrows()]

        if not markets:
            logger.warning(f"No markets found for crypto={crypto}")
            return BacktestResult(
                strategy=strategy_name,
                crypto=crypto or "ALL",
                fill_model=self.fill_model_name,
                params=params,
            )

        logger.info(
            f"Running {strategy_name} | {crypto or 'ALL'} | {self.fill_model_name} | "
            f"{len(markets)} markets | params={params}"
        )

        all_trades: List[BacktestTrade] = []

        for market in markets:
            trades_df = self.dm.get_trades_for_market(market.condition_id)

            try:
                trades = self.strategy.generate_trades(market, trades_df, params)
                all_trades.extend(trades)
            except Exception as e:
                logger.debug(f"Error on market {market.condition_id}: {e}")
                continue

        result = self._compute_metrics(
            trades=all_trades,
            strategy=strategy_name,
            crypto=crypto or "ALL",
            params=params,
        )

        elapsed = time.time() - start_time
        logger.debug(f"Backtest completed in {elapsed:.2f}s — {len(all_trades)} trades generated")

        return result

    def _row_to_market(self, row) -> Market:
        """Convert a DataFrame row to a Market object."""
        from datetime import date, datetime
        from models.market import CryptoAsset, Direction, PeriodType

        m = Market(
            condition_id=str(row["condition_id"]),
            question=str(row.get("question", "")),
            event_market_name=str(row.get("event_market_name", "")),
            total_volume_usd=float(row.get("total_volume_usd", 0)),
            trade_count=int(row.get("trade_count", 0)),
        )

        # Crypto
        crypto_val = row.get("crypto")
        if crypto_val:
            try:
                m.crypto = CryptoAsset(crypto_val)
            except ValueError:
                pass

        # Threshold
        threshold = row.get("threshold")
        if threshold is not None:
            try:
                m.threshold = float(threshold)
            except (ValueError, TypeError):
                pass

        # Direction
        direction = row.get("direction")
        if direction:
            try:
                m.direction = Direction(direction)
            except ValueError:
                pass

        # Resolution date
        res_date = row.get("resolution_date")
        if res_date:
            try:
                m.resolution_date = date.fromisoformat(str(res_date))
            except ValueError:
                pass

        # Outcome
        outcome = row.get("resolved_outcome", "UNKNOWN")
        try:
            m.resolved_outcome = Outcome(outcome)
        except ValueError:
            m.resolved_outcome = Outcome.UNKNOWN

        # Resolution time
        res_time = row.get("resolution_time")
        if res_time and str(res_time) not in ("None", "nan", ""):
            try:
                m.resolution_time = datetime.fromisoformat(str(res_time))
            except ValueError:
                pass

        return m

    def _compute_metrics(
        self,
        trades: List[BacktestTrade],
        strategy: str,
        crypto: str,
        params: dict,
    ) -> BacktestResult:
        """Compute all performance metrics from the list of BacktestTrades."""

        result = BacktestResult(
            strategy=strategy,
            crypto=crypto,
            fill_model=self.fill_model_name,
            params=params,
        )

        if not trades:
            return result

        filled = [t for t in trades if t.filled]
        result.total_trades = len(trades)
        result.filled_trades = len(filled)
        result.fill_rate = len(filled) / max(len(trades), 1)

        if not filled:
            return result

        winning = [t for t in filled if t.net_pnl > 0]
        losing = [t for t in filled if t.net_pnl <= 0]

        result.winning_trades = len(winning)
        result.losing_trades = len(losing)
        result.win_rate = len(winning) / max(len(filled), 1)

        result.total_capital_deployed = sum(t.capital for t in filled)
        result.total_gross_pnl = sum(t.gross_pnl for t in filled)
        result.total_fees = sum(t.fees_paid for t in filled)
        result.total_net_pnl = sum(t.net_pnl for t in filled)

        # ROI
        if result.total_capital_deployed > 0:
            result.roi_pct = (result.total_net_pnl / result.total_capital_deployed) * 100

        # Profit factor
        gross_wins = sum(t.net_pnl for t in winning)
        gross_losses = abs(sum(t.net_pnl for t in losing))
        result.profit_factor = gross_wins / max(gross_losses, 0.01)

        # Sharpe ratio — using weekly P&L
        weekly_pnl = self._group_pnl_by_period(filled, period="W")
        result.weekly_pnl = weekly_pnl
        result.monthly_pnl = self._group_pnl_by_period(filled, period="ME")

        from analysis.metrics import _sharpe
        result.sharpe_ratio = _sharpe(list(weekly_pnl.values()))

        # Max drawdown — cumulative P&L series
        result.max_drawdown = self._compute_max_drawdown(filled)

        return result

    def _group_pnl_by_period(self, trades: List[BacktestTrade], period: str = "W") -> dict:
        """Group P&L by week or month. Returns {period_str: total_pnl}."""
        if not trades:
            return {}

        records = []
        for t in trades:
            ref_time = t.fill_time or t.exit_time
            if ref_time:
                records.append({"time": ref_time, "pnl": t.net_pnl})

        if not records:
            return {}

        df = pd.DataFrame(records)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()
        grouped = df["pnl"].resample(period).sum()
        return {str(k): float(v) for k, v in grouped.items()}

    def _compute_max_drawdown(self, trades: List[BacktestTrade]) -> float:
        """Compute max drawdown from cumulative P&L series."""
        if not trades:
            return 0.0

        records = sorted(
            [(t.fill_time or t.exit_time, t.net_pnl) for t in trades if t.fill_time or t.exit_time],
            key=lambda x: x[0]
        )

        if not records:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for _, pnl in records:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return max_dd
