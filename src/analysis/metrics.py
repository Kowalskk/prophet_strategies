"""
PROPHET STRATEGIES
Metrics — computes all performance metrics from a list of BacktestTrades
"""
from __future__ import annotations
import logging
from typing import List

import numpy as np
import pandas as pd

from models.trade import BacktestTrade, BacktestResult

logger = logging.getLogger(__name__)


def compute_metrics(
    trades: List[BacktestTrade],
    strategy: str,
    crypto: str,
    fill_model: str,
    params: dict,
) -> BacktestResult:
    """
    Full metrics computation from a list of BacktestTrades.
    Returns a populated BacktestResult.
    """
    result = BacktestResult(
        strategy=strategy,
        crypto=crypto,
        fill_model=fill_model,
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
    losing  = [t for t in filled if t.net_pnl <= 0]

    result.winning_trades = len(winning)
    result.losing_trades  = len(losing)
    result.win_rate       = len(winning) / max(len(filled), 1)

    result.total_capital_deployed = sum(t.capital for t in filled)
    result.total_gross_pnl        = sum(t.gross_pnl for t in filled)
    result.total_fees             = sum(t.fees_paid for t in filled)
    result.total_net_pnl          = sum(t.net_pnl for t in filled)

    if result.total_capital_deployed > 0:
        result.roi_pct = (result.total_net_pnl / result.total_capital_deployed) * 100

    gross_wins   = sum(t.net_pnl for t in winning)
    gross_losses = abs(sum(t.net_pnl for t in losing))
    result.profit_factor = gross_wins / max(gross_losses, 0.01)

    # Weekly / monthly P&L
    result.weekly_pnl  = _group_pnl(filled, "W")
    result.monthly_pnl = _group_pnl(filled, "ME")   # ME = month-end frequency

    # Sharpe (weekly)
    result.sharpe_ratio  = _sharpe(list(result.weekly_pnl.values()))

    # Max drawdown
    result.max_drawdown = _max_drawdown(filled)

    return result


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _group_pnl(trades: List[BacktestTrade], freq: str) -> dict:
    records = []
    for t in trades:
        ref = t.fill_time or t.exit_time
        if ref:
            records.append({"time": ref, "pnl": t.net_pnl})
    if not records:
        return {}
    df = pd.DataFrame(records)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    grouped = df["pnl"].resample(freq).sum()
    return {str(k): float(v) for k, v in grouped.items()}


def _sharpe(weekly_pnl: list[float]) -> float:
    # Need at least 4 weeks for a meaningful Sharpe ratio
    if len(weekly_pnl) < 4:
        return 0.0
    arr = np.array(weekly_pnl)
    std = arr.std()
    if std == 0:
        return 0.0
    raw = float(arr.mean() / std)
    # Clamp to [-10, 10] — extreme values indicate data/period issues
    return float(np.clip(raw, -10.0, 10.0))


def _max_drawdown(trades: List[BacktestTrade]) -> float:
    records = sorted(
        [(t.fill_time or t.exit_time, t.net_pnl)
         for t in trades if (t.fill_time or t.exit_time)],
        key=lambda x: x[0],
    )
    if not records:
        return 0.0
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for _, pnl in records:
        cum += pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def result_to_row(r: BacktestResult) -> dict:
    """Flatten a BacktestResult to a single dict row (for CSV/DataFrame)."""
    row = {
        "strategy":               r.strategy,
        "crypto":                 r.crypto,
        "fill_model":             r.fill_model,
        "total_trades":           r.total_trades,
        "filled_trades":          r.filled_trades,
        "fill_rate":              round(r.fill_rate, 4),
        "winning_trades":         r.winning_trades,
        "losing_trades":          r.losing_trades,
        "win_rate":               round(r.win_rate, 4),
        "total_capital_deployed": round(r.total_capital_deployed, 2),
        "total_net_pnl":          round(r.total_net_pnl, 2),
        "total_fees":             round(r.total_fees, 2),
        "roi_pct":                round(r.roi_pct, 2),
        "profit_factor":          round(r.profit_factor, 4),
        "sharpe_ratio":           round(r.sharpe_ratio, 4),
        "max_drawdown":           round(r.max_drawdown, 2),
    }
    # Flatten params
    for k, v in r.params.items():
        row[f"param_{k}"] = v
    return row
