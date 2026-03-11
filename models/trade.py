"""
PROPHET STRATEGIES
Trade dataclasses — raw market trade and backtest result
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from models.market import Outcome


@dataclass
class MarketTrade:
    """A single trade from Polymarket historical data."""
    block_time: datetime
    condition_id: str
    token_outcome: str          # "Yes" | "No"
    price: float                # 0.0 - 1.0
    amount: float               # USD volume
    shares: float
    fee: float
    maker: str
    taker: str
    neg_risk: bool = False


@dataclass
class BacktestTrade:
    """A simulated trade in the backtest."""
    condition_id: str
    strategy: str
    crypto: str
    side: str                   # "YES" | "NO"
    entry_price: float
    capital: float              # USD risked
    shares_bought: float
    fill_model: str             # "optimistic" | "realistic"
    exit_strategy: str

    # Fill result
    filled: bool = False
    fill_time: Optional[datetime] = None
    fill_price: float = 0.0
    fill_slippage: float = 0.0

    # Exit result
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = ""       # "resolution" | "sell_at_Nx" | "unfilled"

    # Resolution
    resolved_outcome: Outcome = Outcome.UNKNOWN

    # P&L
    gross_pnl: float = 0.0
    fees_paid: float = 0.0
    net_pnl: float = 0.0

    @property
    def roi_pct(self) -> float:
        if self.capital == 0:
            return 0.0
        return (self.net_pnl / self.capital) * 100

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0


@dataclass
class BacktestResult:
    """Aggregated result of a full backtest run."""
    strategy: str
    crypto: str
    fill_model: str
    params: dict

    total_trades: int = 0
    filled_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    total_capital_deployed: float = 0.0
    total_gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_net_pnl: float = 0.0

    win_rate: float = 0.0
    fill_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    roi_pct: float = 0.0

    weekly_pnl: dict = None     # {week_str: pnl}
    monthly_pnl: dict = None    # {month_str: pnl}

    def __post_init__(self):
        if self.weekly_pnl is None:
            self.weekly_pnl = {}
        if self.monthly_pnl is None:
            self.monthly_pnl = {}
