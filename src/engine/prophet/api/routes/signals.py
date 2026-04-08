"""
Signals routes.

GET  /signals          → recent signals (paginated), joined with market info
GET  /signals/summary  → counts by strategy and status
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/signals", tags=["signals"])


class SignalMarket(BaseModel):
    crypto: str | None
    threshold: float | None
    direction: str | None
    resolution_date: str | None


class SignalResponse(BaseModel):
    id: int
    market_id: int
    strategy: str
    side: str
    target_price: float
    size_usd: float
    confidence: float
    status: str
    created_at: str
    market: SignalMarket | None = None


class SignalListResponse(BaseModel):
    items: list[SignalResponse]
    total: int
    limit: int
    offset: int


class SignalSummaryItem(BaseModel):
    strategy: str
    status: str
    count: int


@router.get("", response_model=SignalListResponse)
async def list_signals(
    strategy: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SignalListResponse:
    """Return recent signals, newest first, with market metadata."""
    from prophet.db.models import Market, Signal

    # Base query
    stmt = select(Signal).order_by(Signal.created_at.desc())
    count_stmt = select(func.count()).select_from(Signal)

    if strategy:
        stmt = stmt.where(Signal.strategy == strategy)
        count_stmt = count_stmt.where(Signal.strategy == strategy)
    if status:
        stmt = stmt.where(Signal.status == status)
        count_stmt = count_stmt.where(Signal.status == status)

    total = (await db.execute(count_stmt)).scalar_one() or 0
    result = await db.execute(stmt.limit(limit).offset(offset))
    signals = result.scalars().all()

    # Batch-load markets
    market_ids = list({s.market_id for s in signals})
    market_map: dict[int, Market] = {}
    if market_ids:
        mkt_result = await db.execute(
            select(Market).where(Market.id.in_(market_ids))
        )
        for m in mkt_result.scalars().all():
            market_map[m.id] = m

    items = []
    for sig in signals:
        mkt = market_map.get(sig.market_id)
        market_info = None
        if mkt:
            market_info = SignalMarket(
                crypto=mkt.crypto,
                threshold=mkt.threshold,
                direction=mkt.direction,
                resolution_date=mkt.resolution_date.isoformat() if mkt.resolution_date else None,
            )
        items.append(
            SignalResponse(
                id=sig.id,
                market_id=sig.market_id,
                strategy=sig.strategy,
                side=sig.side,
                target_price=sig.target_price,
                size_usd=sig.size_usd,
                confidence=sig.confidence,
                status=sig.status,
                created_at=sig.created_at.isoformat() if sig.created_at else "",
                market=market_info,
            )
        )

    return SignalListResponse(items=items, total=int(total), limit=limit, offset=offset)


@router.get("/summary", response_model=list[SignalSummaryItem])
async def signals_summary(
    db: AsyncSession = Depends(get_db),
) -> list[SignalSummaryItem]:
    """Return signal counts grouped by strategy and status."""
    from prophet.db.models import Signal

    stmt = (
        select(Signal.strategy, Signal.status, func.count().label("count"))
        .group_by(Signal.strategy, Signal.status)
        .order_by(Signal.strategy, Signal.status)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return [SignalSummaryItem(strategy=r[0], status=r[1], count=r[2]) for r in rows]
