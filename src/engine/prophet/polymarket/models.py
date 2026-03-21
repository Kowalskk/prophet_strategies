"""
Pydantic v2 models for Polymarket API responses.

These are *transport* models — they represent data shapes returned by the
CLOB and Gamma APIs.  They are intentionally separate from the SQLAlchemy ORM
models in ``prophet.db.models`` so that the API integration layer stays
independent of the database layer.

Models
------
- OrderBookLevel   — single price level (price + size)
- OrderBook        — full order book with bids + asks + derived metrics
- Trade            — a single observed CLOB trade
- PriceData        — spot price from Binance / CoinGecko
- MarketInfo       — lightweight market summary from the CLOB API
- PolymarketMarket — rich market record from the Gamma API
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Order Book
# ---------------------------------------------------------------------------


class OrderBookLevel(BaseModel):
    """A single price level in an order book.

    Prices on Polymarket are in [0, 1] (USD per outcome share).
    Sizes are in shares (not USD).
    """

    price: float = Field(..., description="Price per share in USD [0, 1].")
    size: float = Field(..., description="Number of shares available at this price.")

    @field_validator("price", "size", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float:
        """CLOB API returns prices/sizes as strings — coerce to float."""
        return float(v)


class OrderBook(BaseModel):
    """Processed order book for a single token (YES or NO side).

    The raw bids and asks are stored as-is; derived metrics (spread, depth)
    are computed by :func:`~prophet.polymarket.orderbook.compute_metrics`.
    """

    token_id: str = Field(..., description="CLOB token ID this book belongs to.")
    bids: list[OrderBookLevel] = Field(default_factory=list, description="Bids sorted descending by price.")
    asks: list[OrderBookLevel] = Field(default_factory=list, description="Asks sorted ascending by price.")
    timestamp: datetime = Field(..., description="UTC time at which the snapshot was taken.")

    # Derived metrics — populated by orderbook.compute_metrics()
    best_bid: float | None = Field(default=None, description="Highest bid price.")
    best_ask: float | None = Field(default=None, description="Lowest ask price.")
    spread_pct: float | None = Field(
        default=None,
        description="(best_ask - best_bid) / best_ask * 100. None if either side is empty.",
    )
    bid_depth_10pct: float = Field(
        default=0.0,
        description="Total USD available within 10 % of the best bid price.",
    )
    ask_depth_10pct: float = Field(
        default=0.0,
        description="Total USD available within 10 % of the best ask price.",
    )
    mid_price: float | None = Field(
        default=None,
        description="(best_bid + best_ask) / 2. None if either side is empty.",
    )


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class Trade(BaseModel):
    """A single trade observed on the CLOB."""

    trade_id: str = Field(default="", description="Unique trade ID from CLOB.")
    token_id: str = Field(..., description="CLOB token ID.")
    side: str = Field(..., description="BUY or SELL (taker perspective).")
    price: float = Field(..., description="Execution price per share.")
    size: float = Field(..., description="Number of shares traded.")
    size_usd: float = Field(..., description="Notional value in USD (price * size).")
    timestamp: datetime = Field(..., description="UTC time of the trade.")
    maker_address: str = Field(default="", description="Maker wallet address.")
    taker_address: str = Field(default="", description="Taker wallet address.")

    @field_validator("price", "size", "size_usd", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float:
        return float(v)


# ---------------------------------------------------------------------------
# Price Data
# ---------------------------------------------------------------------------


class PriceData(BaseModel):
    """Spot price of a crypto asset fetched from an external price feed."""

    symbol: str = Field(..., description="Crypto symbol, e.g. 'BTC'.")
    price_usd: float = Field(..., description="Current USD price.")
    source: str = Field(..., description="Data source: 'binance' or 'coingecko'.")
    timestamp: datetime = Field(..., description="UTC time the price was fetched.")

    @field_validator("price_usd", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float:
        return float(v)


# ---------------------------------------------------------------------------
# CLOB Market Info
# ---------------------------------------------------------------------------


class MarketInfo(BaseModel):
    """Lightweight market record as returned by the CLOB /markets endpoint.

    Contains just enough to identify a market and its tradeable tokens.
    For richer metadata (question text, resolution date, category) use
    :class:`PolymarketMarket` from the Gamma API.
    """

    condition_id: str = Field(..., description="Polymarket condition ID.")
    tokens: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of token dicts with 'token_id' and 'outcome' keys.",
    )
    active: bool = Field(default=True, description="Whether the market is currently active.")
    closed: bool = Field(default=False, description="Whether the market is closed/resolved.")
    accepting_orders: bool = Field(default=True, description="Whether the CLOB accepts orders.")
    minimum_order_size: float = Field(default=5.0, description="Minimum order size in USD.")
    minimum_tick_size: float = Field(default=0.01, description="Minimum price increment.")

    @property
    def token_id_yes(self) -> str | None:
        """Return the YES token ID, or None if not found."""
        for t in self.tokens:
            if str(t.get("outcome", "")).upper() == "YES":
                return t.get("token_id")
        return None

    @property
    def token_id_no(self) -> str | None:
        """Return the NO token ID, or None if not found."""
        for t in self.tokens:
            if str(t.get("outcome", "")).upper() == "NO":
                return t.get("token_id")
        return None


# ---------------------------------------------------------------------------
# Gamma Market
# ---------------------------------------------------------------------------


class PolymarketMarket(BaseModel):
    """Rich market record as returned by the Gamma API.

    The Gamma API provides market metadata, resolution details, and outcome
    token information in a single response.  This model normalises that data
    for use by the :class:`~prophet.core.scanner.MarketScanner`.
    """

    id: str = Field(..., description="Gamma internal market ID.")
    condition_id: str = Field(default="", description="On-chain condition ID (hex).")
    question: str = Field(..., description="Full market question text.")
    slug: str = Field(default="", description="URL slug for the market page.")
    description: str = Field(default="", description="Long-form market description.")

    # Status
    active: bool = Field(default=True)
    closed: bool = Field(default=False)
    archived: bool = Field(default=False)
    accepting_orders: bool = Field(default=True)
    accepting_order_timestamp: str | None = Field(default=None)

    # Resolution
    end_date_iso: str | None = Field(default=None, description="ISO 8601 resolution date string.")
    game_start_time: str | None = Field(default=None)
    resolution_source: str = Field(default="")
    resolved: bool = Field(default=False)
    outcome: str | None = Field(default=None, description="Resolved outcome (YES/NO) if resolved.")

    # Tokens
    clob_token_ids: list[str] = Field(
        default_factory=list,
        description="[yes_token_id, no_token_id] ordered by outcome index.",
    )
    tokens: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Full token records from Gamma, each with 'token_id' and 'outcome'.",
    )

    # Pricing (last known)
    last_trade_price: float | None = Field(default=None)
    best_bid: float | None = Field(default=None)
    best_ask: float | None = Field(default=None)

    # Liquidity
    volume: float = Field(default=0.0, description="Total traded volume in USD.")
    liquidity: float = Field(default=0.0, description="Current on-book liquidity in USD.")

    # Tags / categories
    tags: list[dict[str, Any]] = Field(default_factory=list)

    # Raw payload — preserved for debugging
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    model_config = {"populate_by_name": True, "extra": "allow"}

    @field_validator("last_trade_price", "best_bid", "best_ask", "volume", "liquidity", mode="before")
    @classmethod
    def coerce_optional_float(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @property
    def token_id_yes(self) -> str | None:
        """YES token ID from the tokens list, falling back to clob_token_ids[0]."""
        for t in self.tokens:
            if str(t.get("outcome", "")).upper() == "YES":
                return t.get("token_id")
        if self.clob_token_ids:
            return self.clob_token_ids[0]
        return None

    @property
    def token_id_no(self) -> str | None:
        """NO token ID from the tokens list, falling back to clob_token_ids[1]."""
        for t in self.tokens:
            if str(t.get("outcome", "")).upper() == "NO":
                return t.get("token_id")
        if len(self.clob_token_ids) > 1:
            return self.clob_token_ids[1]
        return None
