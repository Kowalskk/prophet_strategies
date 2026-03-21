"""
Pydantic v2 response models for all REST API endpoints.

All models use ``model_config = ConfigDict(from_attributes=True)`` so they can
be constructed directly from SQLAlchemy ORM instances via ``model_validate``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


class MessageResponse(BaseModel):
    """Simple acknowledgement response."""

    message: str


class ErrorResponse(BaseModel):
    """Standard error envelope."""

    error: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """GET /health"""

    status: str = "ok"
    version: str
    uptime_seconds: float
    paper_trading: bool


class SystemStatusResponse(BaseModel):
    """GET /status"""

    scanning_active: bool
    last_scan_at: datetime | None
    open_positions: int
    daily_pnl: float
    kill_switch: bool


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------


class MarketResponse(BaseModel):
    """Single market detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    condition_id: str
    question: str
    crypto: str
    threshold: float | None
    direction: str | None
    resolution_date: date | None
    token_id_yes: str
    token_id_no: str
    status: str
    resolved_outcome: str | None
    resolution_time: datetime | None
    created_at: datetime
    updated_at: datetime


class MarketListResponse(BaseModel):
    """Paginated list of markets."""

    items: list[MarketResponse]
    total: int
    limit: int
    offset: int


class OrderBookLevelResponse(BaseModel):
    """A single price level in an order book."""

    price: float
    size: float


class OrderBookResponse(BaseModel):
    """Cached order book snapshot for a market token side."""

    market_id: int
    token_id: str
    side: str
    best_bid: float | None
    best_ask: float | None
    spread_pct: float | None
    bid_depth_10pct: float
    ask_depth_10pct: float
    bids: list[OrderBookLevelResponse] = Field(default_factory=list)
    asks: list[OrderBookLevelResponse] = Field(default_factory=list)
    timestamp: datetime | None = None


class ObservedTradeResponse(BaseModel):
    """A single observed on-chain trade."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    token_id: str
    side: str
    price: float
    size_usd: float
    timestamp: datetime
    maker: str
    taker: str


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class StrategyResponse(BaseModel):
    """Strategy listing entry."""

    name: str
    description: str
    default_params: dict[str, Any]
    enabled: bool


class StrategyConfigResponse(BaseModel):
    """Strategy config row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy: str
    market_id: int | None
    crypto: str | None
    enabled: bool
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


class PositionResponse(BaseModel):
    """An open or closed position."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    strategy: str
    side: str
    entry_price: float
    size_usd: float
    shares: float
    status: str
    opened_at: datetime
    closed_at: datetime | None
    exit_price: float | None
    exit_reason: str | None
    gross_pnl: float | None
    fees: float | None
    net_pnl: float | None
    # Live P&L estimate (only for open positions)
    unrealized_pnl: float | None = None
    current_price: float | None = None


class PositionListResponse(BaseModel):
    """List of open positions."""

    items: list[PositionResponse]
    total: int


class ClosedPositionResponse(BaseModel):
    """Paginated list of closed positions."""

    items: list[PositionResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class PerformanceSummaryResponse(BaseModel):
    """Overall performance statistics."""

    total_pnl: float
    win_rate: float
    sharpe_ratio: float
    profit_factor: float
    max_drawdown: float
    total_trades: int
    open_positions: int


class PnLPointResponse(BaseModel):
    """Single daily P&L data point for charting."""

    date: str  # YYYY-MM-DD
    pnl: float


class StrategyBreakdownResponse(BaseModel):
    """P&L breakdown per strategy or per crypto."""

    name: str  # strategy name or crypto symbol
    net_pnl: float
    trades: int
    win_rate: float


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    """Current system configuration and risk limits."""

    # Trading mode
    paper_trading: bool
    kill_switch: bool

    # Scanner
    scan_interval_minutes: int
    target_cryptos: list[str]

    # API
    api_host: str
    api_port: int

    # Risk limits
    max_position_per_market: float
    max_daily_loss: float
    max_open_positions: int
    max_concentration: float
    max_drawdown_total: float


class RiskMetricsResponse(BaseModel):
    """Current risk utilisation percentages."""

    kill_switch: bool
    paper_trading: bool
    daily_loss_pct: float
    open_positions_pct: float
    drawdown_pct: float
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


class SignalResponse(BaseModel):
    """A generated trade signal."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    strategy: str
    side: str
    target_price: float
    size_usd: float
    confidence: float
    params: dict[str, Any]
    status: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


class SpotPriceResponse(BaseModel):
    """Latest spot price for one crypto."""

    crypto: str
    price_usd: float
    source: str
    timestamp: datetime | None


class SpotPricesResponse(BaseModel):
    """Latest spot prices for all tracked cryptos."""

    prices: list[SpotPriceResponse]


class OrderBookSnapshotHistoryItem(BaseModel):
    """One row from the orderbook_snapshots table."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    market_id: int
    token_id: str
    side: str
    timestamp: datetime
    best_bid: float | None
    best_ask: float | None
    bid_depth_10pct: float
    ask_depth_10pct: float
    spread_pct: float | None


class SnapshotHistoryResponse(BaseModel):
    """History of order book snapshots for a market."""

    market_id: int
    items: list[OrderBookSnapshotHistoryItem]
