"""
Markets routes.

GET /markets                        → paginated market list
GET /markets/{condition_id}         → single market detail
GET /markets/{condition_id}/orderbook → cached order book from Redis
GET /markets/{condition_id}/trades  → recent observed trades
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import (
    MarketListResponse,
    MarketResponse,
    ObservedTradeResponse,
    OrderBookLevelResponse,
    OrderBookResponse,
)
from prophet.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("", response_model=MarketListResponse)
async def list_markets(
    crypto: str | None = Query(None, description="Filter by crypto: BTC, ETH, SOL"),
    status: Literal["active", "resolved", "all"] = Query("all"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> MarketListResponse:
    """List all markets with optional filters."""
    from prophet.db.models import Market

    stmt = select(Market)
    count_stmt = select(func.count()).select_from(Market)

    if crypto:
        stmt = stmt.where(Market.crypto == crypto.upper())
        count_stmt = count_stmt.where(Market.crypto == crypto.upper())

    if status != "all":
        stmt = stmt.where(Market.status == status)
        count_stmt = count_stmt.where(Market.status == status)

    total = (await db.execute(count_stmt)).scalar_one() or 0

    stmt = stmt.order_by(Market.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    markets = result.scalars().all()

    return MarketListResponse(
        items=[MarketResponse.model_validate(m) for m in markets],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{condition_id}", response_model=MarketResponse)
async def get_market(
    condition_id: str,
    db: AsyncSession = Depends(get_db),
) -> MarketResponse:
    """Get a single market by condition_id."""
    from prophet.db.models import Market

    stmt = select(Market).where(Market.condition_id == condition_id)
    result = await db.execute(stmt)
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail=f"Market {condition_id!r} not found.")
    return MarketResponse.model_validate(market)


@router.get("/{condition_id}/orderbook", response_model=OrderBookResponse)
async def get_orderbook(
    condition_id: str,
    side: Literal["yes", "no"] = Query("yes"),
    db: AsyncSession = Depends(get_db),
) -> OrderBookResponse:
    """Return cached order book from Redis (falls back to latest DB snapshot)."""
    from prophet.db.models import Market, OrderBookSnapshot

    # Resolve market
    stmt = select(Market).where(Market.condition_id == condition_id)
    result = await db.execute(stmt)
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail=f"Market {condition_id!r} not found.")

    # Try Redis cache first
    try:
        import redis.asyncio as aioredis

        from prophet.config import settings

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        cache_key = f"ob:{market.id}:{side}"
        cached = await redis_client.get(cache_key)
        await redis_client.aclose()

        if cached:
            data = json.loads(cached)
            bids = [
                OrderBookLevelResponse(price=lvl["price"], size=lvl["size"])
                for lvl in data.get("bids", [])
            ]
            asks = [
                OrderBookLevelResponse(price=lvl["price"], size=lvl["size"])
                for lvl in data.get("asks", [])
            ]
            return OrderBookResponse(
                market_id=market.id,
                token_id=data.get("token_id", ""),
                side=side,
                best_bid=data.get("best_bid"),
                best_ask=data.get("best_ask"),
                spread_pct=data.get("spread_pct"),
                bid_depth_10pct=data.get("bid_depth_10pct", 0.0),
                ask_depth_10pct=data.get("ask_depth_10pct", 0.0),
                bids=bids,
                asks=asks,
                timestamp=None,
            )
    except Exception as exc:
        logger.debug("Redis orderbook cache miss: %s", exc)

    # Fallback: latest DB snapshot
    token_id = market.token_id_yes if side == "yes" else market.token_id_no
    stmt2 = (
        select(OrderBookSnapshot)
        .where(
            OrderBookSnapshot.market_id == market.id,
            OrderBookSnapshot.side == side,
        )
        .order_by(OrderBookSnapshot.timestamp.desc())
        .limit(1)
    )
    result2 = await db.execute(stmt2)
    snap = result2.scalar_one_or_none()

    if snap is None:
        return OrderBookResponse(
            market_id=market.id,
            token_id=token_id,
            side=side,
            best_bid=None,
            best_ask=None,
            spread_pct=None,
            bid_depth_10pct=0.0,
            ask_depth_10pct=0.0,
            bids=[],
            asks=[],
            timestamp=None,
        )

    raw = snap.raw_book or {}
    bids = [
        OrderBookLevelResponse(price=lvl["price"], size=lvl["size"])
        for lvl in raw.get("bids", [])
    ]
    asks = [
        OrderBookLevelResponse(price=lvl["price"], size=lvl["size"])
        for lvl in raw.get("asks", [])
    ]
    return OrderBookResponse(
        market_id=market.id,
        token_id=snap.token_id,
        side=side,
        best_bid=snap.best_bid,
        best_ask=snap.best_ask,
        spread_pct=snap.spread_pct,
        bid_depth_10pct=snap.bid_depth_10pct,
        ask_depth_10pct=snap.ask_depth_10pct,
        bids=bids,
        asks=asks,
        timestamp=snap.timestamp,
    )


@router.get("/{condition_id}/trades", response_model=list[ObservedTradeResponse])
async def get_trades(
    condition_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[ObservedTradeResponse]:
    """Return recent observed trades for a market."""
    from prophet.db.models import Market, ObservedTrade

    stmt = select(Market).where(Market.condition_id == condition_id)
    result = await db.execute(stmt)
    market = result.scalar_one_or_none()
    if market is None:
        raise HTTPException(status_code=404, detail=f"Market {condition_id!r} not found.")

    stmt2 = (
        select(ObservedTrade)
        .where(ObservedTrade.market_id == market.id)
        .order_by(ObservedTrade.timestamp.desc())
        .limit(limit)
    )
    result2 = await db.execute(stmt2)
    trades = result2.scalars().all()
    return [ObservedTradeResponse.model_validate(t) for t in trades]
