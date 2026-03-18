"""
Data routes.

GET /data/prices                   → latest spot prices for BTC/ETH/SOL
GET /data/snapshots/{market_id}    → order book snapshot history for a market
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import (
    OrderBookSnapshotHistoryItem,
    SnapshotHistoryResponse,
    SpotPriceResponse,
    SpotPricesResponse,
)
from prophet.config import settings
from prophet.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/data", tags=["data"])


@router.get("/prices", response_model=SpotPricesResponse)
async def get_prices(db: AsyncSession = Depends(get_db)) -> SpotPricesResponse:
    """Return latest spot prices for BTC/ETH/SOL.

    Reads from Redis first; falls back to the latest DB row per crypto.
    """
    from prophet.db.models import PriceSnapshot

    prices: list[SpotPriceResponse] = []

    for crypto in settings.target_cryptos:
        price_usd: float | None = None
        source = "db"
        timestamp: datetime | None = None

        # Try Redis
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
            cached = await redis_client.get(f"price:{crypto}")
            await redis_client.aclose()
            if cached:
                data = json.loads(cached)
                price_usd = float(data["price_usd"])
                source = data.get("source", "cache")
                ts_str = data.get("timestamp")
                if ts_str:
                    timestamp = datetime.fromisoformat(ts_str)
        except Exception as exc:
            logger.debug("Redis price cache miss for %s: %s", crypto, exc)

        # Fallback: DB
        if price_usd is None:
            try:
                stmt = (
                    select(PriceSnapshot)
                    .where(PriceSnapshot.crypto == crypto)
                    .order_by(PriceSnapshot.timestamp.desc())
                    .limit(1)
                )
                result = await db.execute(stmt)
                snap = result.scalar_one_or_none()
                if snap is not None:
                    price_usd = snap.price_usd
                    source = snap.source
                    timestamp = snap.timestamp
            except Exception as exc:
                logger.warning("DB price fetch failed for %s: %s", crypto, exc)

        prices.append(
            SpotPriceResponse(
                crypto=crypto,
                price_usd=price_usd or 0.0,
                source=source,
                timestamp=timestamp,
            )
        )

    return SpotPricesResponse(prices=prices)


@router.get("/snapshots/{market_id}", response_model=SnapshotHistoryResponse)
async def get_snapshots(
    market_id: int,
    side: str = Query("yes", pattern="^(yes|no)$"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> SnapshotHistoryResponse:
    """Return order book snapshot history for a market."""
    from prophet.db.models import OrderBookSnapshot

    stmt = (
        select(OrderBookSnapshot)
        .where(
            OrderBookSnapshot.market_id == market_id,
            OrderBookSnapshot.side == side,
        )
        .order_by(OrderBookSnapshot.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    items = [OrderBookSnapshotHistoryItem.model_validate(s) for s in snapshots]
    return SnapshotHistoryResponse(market_id=market_id, items=items)
