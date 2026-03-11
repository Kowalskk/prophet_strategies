"""
PROPHET STRATEGIES
Optimizer — generates parameter grid combinations and ranks results
"""
from __future__ import annotations
import itertools
import logging
from typing import Iterator

import pandas as pd

logger = logging.getLogger(__name__)

# All supported exit strategies including new multipliers
ALL_EXIT_STRATEGIES = [
    "hold_to_resolution",
    "sell_at_2x", "sell_at_5x", "sell_at_10x",
    "sell_at_15x", "sell_at_25x", "sell_at_50x",
    "sell_at_75x", "sell_at_100x", "sell_at_125x", "sell_at_150x",
]


def generate_stink_bid_combos(cfg: dict) -> Iterator[dict]:
    """
    Yield all parameter combinations for stink_bid from config grid.
    Each combo is a flat dict ready to pass to BacktestEngine.run().
    """
    grid = cfg["strategies"]["stink_bid"]["grid"]
    cryptos = cfg["data"]["cryptos"]
    fill_models = cfg["simulation"]["fill_models"]

    keys = ["tier1_price", "tier2_price", "tier1_capital", "tier2_capital", "exit_strategy"]
    values = [
        grid["tier1_price"],
        grid["tier2_price"],
        grid["tier1_capital"],
        grid["tier2_capital"],
        grid["exit_strategy"],
    ]

    count = 0
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))

        # Skip degenerate: tier2 must be cheaper than tier1
        if params["tier2_price"] >= params["tier1_price"]:
            continue

        for crypto in cryptos:
            for fill_model in fill_models:
                yield {
                    "strategy": "stink_bid",
                    "crypto": crypto,
                    "fill_model": fill_model,
                    "params": params,
                }
                count += 1

    logger.info(f"Stink bid grid: ~{count} combinations")


def generate_volatility_spread_combos(cfg: dict) -> Iterator[dict]:
    """Yield all parameter combinations for volatility_spread."""
    grid = cfg["strategies"]["volatility_spread"]["grid"]
    cryptos = cfg["data"]["cryptos"]
    fill_models = cfg["simulation"]["fill_models"]

    keys = ["spread_percent", "entry_price_max", "capital_per_side",
            "exit_strategy", "sell_target_pct"]
    values = [
        grid["spread_percent"],
        grid["entry_price_max"],
        grid["capital_per_side"],
        grid["exit_strategy"],
        grid["sell_target_pct"],
    ]

    count = 0
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        for crypto in cryptos:
            for fill_model in fill_models:
                yield {
                    "strategy": "volatility_spread",
                    "crypto": crypto,
                    "fill_model": fill_model,
                    "params": params,
                }
                count += 1

    logger.info(f"Volatility spread grid: ~{count} combinations")


def count_combos(cfg: dict) -> dict:
    """Count total combinations without generating them."""
    sb_grid = cfg["strategies"]["stink_bid"]["grid"]
    vs_grid = cfg["strategies"]["volatility_spread"]["grid"]
    cryptos = len(cfg["data"]["cryptos"])
    models  = len(cfg["simulation"]["fill_models"])

    sb = (
        len(sb_grid["tier1_price"]) *
        len(sb_grid["tier2_price"]) *
        len(sb_grid["tier1_capital"]) *
        len(sb_grid["tier2_capital"]) *
        len(sb_grid["exit_strategy"]) *
        cryptos * models
    )

    vs = (
        len(vs_grid["spread_percent"]) *
        len(vs_grid["entry_price_max"]) *
        len(vs_grid["capital_per_side"]) *
        len(vs_grid["exit_strategy"]) *
        len(vs_grid["sell_target_pct"]) *
        cryptos * models
    )

    return {"stink_bid": sb, "volatility_spread": vs, "total": sb + vs}


def rank_results(df: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """
    Rank backtest results using a composite score.

    Scoring criteria (all normalized 0-1, then weighted):
      - total_net_pnl     : 30%  raw profit
      - sharpe_ratio      : 30%  risk-adjusted return
      - win_rate          : 20%  consistency
      - profit_factor     : 20%  win/loss ratio

    Filters applied before ranking:
      - filled_trades >= 10   (statistical significance)
      - fill_rate >= 0.05     (at least 5% of orders fill)
    """
    if df.empty:
        return df

    df = df.copy()

    # Apply filters
    df = df[df["filled_trades"] >= 10]
    df = df[df["fill_rate"] >= 0.05]

    if df.empty:
        logger.warning("No results pass minimum filters (filled_trades>=10, fill_rate>=5%)")
        return df

    # Normalize each metric to 0-1
    def norm(series: pd.Series) -> pd.Series:
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series(0.5, index=series.index)
        return (series - mn) / (mx - mn)

    score = (
        norm(df["total_net_pnl"])  * 0.30 +
        norm(df["sharpe_ratio"])   * 0.30 +
        norm(df["win_rate"])       * 0.20 +
        norm(df["profit_factor"])  * 0.20
    )

    df["composite_score"] = score.round(4)
    df = df.sort_values("composite_score", ascending=False)
    return df.head(top_n)


def best_per_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """Return the single best config per (strategy, crypto, fill_model)."""
    if df.empty:
        return df
    ranked = rank_results(df, top_n=len(df))
    if ranked.empty:
        return ranked
    return (
        ranked
        .sort_values("composite_score", ascending=False)
        .groupby(["strategy", "crypto", "fill_model"], as_index=False)
        .first()
    )
