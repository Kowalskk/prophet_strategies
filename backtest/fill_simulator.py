"""
PROPHET STRATEGIES
Fill Simulator — determines if a limit order would have been filled
based on historical trade data.

Two models:
  optimistic : fill if ANY trade occurred at target price or better
  realistic  : fill only if enough volume existed, with queue competition
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FillResult:
    filled: bool
    fill_price: float           # Actual fill price (may differ from target due to slippage)
    fill_time: Optional[datetime]
    volume_at_level: float      # USD volume seen at or better than target price
    slippage: float             # Price difference from target (0 if exact fill)
    reason: str                 # Why filled/not filled


class FillSimulator:
    """
    Simulates whether a limit order placed at `target_price` would have been
    filled, given historical trade data for the market.
    """

    def __init__(
        self,
        model: str = "realistic",
        queue_multiplier: float = 3.0,
        min_volume_at_level: float = 10.0,
        slippage_bps: float = 50.0,
    ):
        if model not in ("optimistic", "realistic"):
            raise ValueError(f"Unknown fill model: {model}")
        self.model = model
        self.queue_multiplier = queue_multiplier
        self.min_volume_at_level = min_volume_at_level
        self.slippage_pct = slippage_bps / 10_000.0  # Convert bps → decimal

    def simulate(
        self,
        trades_df: pd.DataFrame,
        token_outcome: str,         # "Yes" or "No"
        target_price: float,        # Price we want to buy at (e.g. 0.03)
        order_size_usd: float,      # How much capital we want to deploy
        order_placed_after: Optional[datetime] = None,
    ) -> FillResult:
        """
        Check if a limit order at `target_price` would have been filled.

        For stink bids (buying cheap), we need a seller willing to sell at
        target_price or LOWER (i.e. we get a better deal if price < target).

        Args:
            trades_df: DataFrame with columns [block_time, token_outcome, price, amount, shares]
            token_outcome: "Yes" or "No"
            target_price: Maximum price we're willing to pay per share
            order_size_usd: Capital to deploy (USD)
            order_placed_after: Only consider trades after this timestamp

        Returns:
            FillResult
        """
        if trades_df.empty:
            return FillResult(
                filled=False, fill_price=0.0, fill_time=None,
                volume_at_level=0.0, slippage=0.0, reason="no_trades_in_market"
            )

        # trades_df is already pre-filtered by outcome and block_time is already datetime
        df = trades_df

        if df.empty:
            return FillResult(
                filled=False, fill_price=0.0, fill_time=None,
                volume_at_level=0.0, slippage=0.0, reason="no_trades_in_bucket"
            )

        # Only look at trades AFTER we would have placed the order
        if order_placed_after is not None:
            df = df[df["block_time"] > order_placed_after]

        if df.empty:
            return FillResult(
                filled=False, fill_price=0.0, fill_time=None,
                volume_at_level=0.0, slippage=0.0, reason="no_trades_after_order_time"
            )

        # Find trades at or below our target price (we're buying, so lower = better for us)
        eligible = df[df["price"] <= target_price].copy()

        if eligible.empty:
            return FillResult(
                filled=False, fill_price=0.0, fill_time=None,
                volume_at_level=0.0, slippage=0.0,
                reason=f"no_trades_at_or_below_{target_price:.4f}"
            )

        volume_at_level = float(eligible["amount"].sum())

        if self.model == "optimistic":
            # Fill if any trade existed at our price — instant fill at best available price
            best_trade = eligible.sort_values("price").iloc[0]
            actual_price = float(best_trade["price"])
            slippage = max(0.0, actual_price - target_price)  # positive = paid more

            return FillResult(
                filled=True,
                fill_price=actual_price,
                fill_time=pd.to_datetime(best_trade["block_time"]).to_pydatetime(),
                volume_at_level=volume_at_level,
                slippage=slippage,
                reason="optimistic_fill"
            )

        else:  # realistic
            # Check minimum volume
            if volume_at_level < self.min_volume_at_level:
                return FillResult(
                    filled=False, fill_price=0.0, fill_time=None,
                    volume_at_level=volume_at_level, slippage=0.0,
                    reason=f"insufficient_volume_{volume_at_level:.2f}_usd"
                )

            # Check volume vs our order size + queue competition
            required_volume = order_size_usd * self.queue_multiplier
            if volume_at_level < required_volume:
                return FillResult(
                    filled=False, fill_price=0.0, fill_time=None,
                    volume_at_level=volume_at_level, slippage=0.0,
                    reason=f"queue_competition_need_{required_volume:.1f}_have_{volume_at_level:.1f}"
                )

            # Fill with slippage
            best_trade = eligible.sort_values("price").iloc[0]
            raw_price = float(best_trade["price"])
            actual_price = min(raw_price * (1.0 + self.slippage_pct), target_price)
            actual_price = round(actual_price, 6)
            slippage = max(0.0, actual_price - target_price)

            return FillResult(
                filled=True,
                fill_price=actual_price,
                fill_time=pd.to_datetime(best_trade["block_time"]).to_pydatetime(),
                volume_at_level=volume_at_level,
                slippage=slippage,
                reason="realistic_fill"
            )

    def find_exit_price_level(
        self,
        trades_df: pd.DataFrame,
        token_outcome: str,
        entry_price: float,
        target_multiplier: float,      # e.g. 10.0 for sell_at_10x
        filled_after: datetime,
    ) -> Optional[tuple[float, datetime]]:
        """
        Search for a point where we could have sold at target_multiplier * entry_price.
        Returns (exit_price, exit_time) or None if never reached.

        For sell_at_Nx: we need market price to reach entry_price * N.
        Since prices are capped at 1.0, max multiplier achievable = 1.0 / entry_price.
        e.g. entry at 0.03 → max 33x if price reaches $1.00
        """
        target_price = min(entry_price * target_multiplier, 1.0)

        if trades_df.empty:
            return None

        # Pre-filtered by outcome and block_time already datetime
        df = trades_df

        # Look for trades at or above target price, after we were filled
        eligible = df[
            (df["block_time"] > filled_after) &
            (df["price"] >= target_price)
        ].sort_values("block_time")

        if eligible.empty:
            return None

        first = eligible.iloc[0]
        return (float(first["price"]), pd.to_datetime(first["block_time"]).to_pydatetime())
