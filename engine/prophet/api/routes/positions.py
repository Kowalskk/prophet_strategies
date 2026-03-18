"""
Positions routes.

GET  /positions             → open positions with live P&L estimate
GET  /positions/closed      → closed positions with pagination
POST /positions/{id}/close  → manually close a position
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import (
    ClosedPositionResponse,
    MessageResponse,
    PositionListResponse,
    PositionResponse,
)
from prophet.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/positions", tags=["positions"])


def _position_to_response(pos: object, current_price: float | None = None) -> PositionResponse:
    """Convert a DB Position to a PositionResponse with optional live P&L."""
    unrealized_pnl = None
    if current_price is not None and pos.status == "open":  # type: ignore[attr-defined]
        # P&L = (current_price - entry_price) * shares
        unrealized_pnl = round(
            (current_price - pos.entry_price) * pos.shares, 4  # type: ignore[attr-defined]
        )

    return PositionResponse(
        id=pos.id,  # type: ignore[attr-defined]
        market_id=pos.market_id,  # type: ignore[attr-defined]
        strategy=pos.strategy,  # type: ignore[attr-defined]
        side=pos.side,  # type: ignore[attr-defined]
        entry_price=pos.entry_price,  # type: ignore[attr-defined]
        size_usd=pos.size_usd,  # type: ignore[attr-defined]
        shares=pos.shares,  # type: ignore[attr-defined]
        status=pos.status,  # type: ignore[attr-defined]
        opened_at=pos.opened_at,  # type: ignore[attr-defined]
        closed_at=pos.closed_at,  # type: ignore[attr-defined]
        exit_price=pos.exit_price,  # type: ignore[attr-defined]
        exit_reason=pos.exit_reason,  # type: ignore[attr-defined]
        gross_pnl=pos.gross_pnl,  # type: ignore[attr-defined]
        fees=pos.fees,  # type: ignore[attr-defined]
        net_pnl=pos.net_pnl,  # type: ignore[attr-defined]
        unrealized_pnl=unrealized_pnl,
        current_price=current_price,
    )


async def _get_current_price(market_id: int, side: str) -> float | None:
    """Try to fetch the current best-bid from Redis for unrealized P&L."""
    try:
        import json

        import redis.asyncio as aioredis

        from prophet.config import settings

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        cache_key = f"ob:{market_id}:{side.lower()}"
        cached = await redis_client.get(cache_key)
        await redis_client.aclose()
        if cached:
            data = json.loads(cached)
            return data.get("best_bid")
    except Exception:
        pass
    return None


@router.get("", response_model=PositionListResponse)
async def list_open_positions(
    db: AsyncSession = Depends(get_db),
) -> PositionListResponse:
    """Return all open positions with live unrealized P&L estimates."""
    from prophet.db.models import Position

    stmt = (
        select(Position)
        .where(Position.status == "open")
        .order_by(Position.opened_at.desc())
    )
    result = await db.execute(stmt)
    positions = result.scalars().all()

    items = []
    for pos in positions:
        current_price = await _get_current_price(pos.market_id, pos.side)
        items.append(_position_to_response(pos, current_price))

    return PositionListResponse(items=items, total=len(items))


@router.get("/closed", response_model=ClosedPositionResponse)
async def list_closed_positions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ClosedPositionResponse:
    """Return closed positions with pagination."""
    from prophet.db.models import Position

    count_stmt = (
        select(func.count()).select_from(Position).where(Position.status == "closed")
    )
    total = (await db.execute(count_stmt)).scalar_one() or 0

    stmt = (
        select(Position)
        .where(Position.status == "closed")
        .order_by(Position.closed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    positions = result.scalars().all()

    items = [_position_to_response(p) for p in positions]
    return ClosedPositionResponse(
        items=items,
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("/{position_id}/close", response_model=MessageResponse)
async def close_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Manually close an open position."""
    from prophet.db.models import Position

    pos = await db.get(Position, position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found.")
    if pos.status != "open":
        raise HTTPException(
            status_code=400,
            detail=f"Position {position_id} is already {pos.status!r}.",
        )

    now = datetime.now(timezone.utc)

    # Try to get current price for exit
    current_price = await _get_current_price(pos.market_id, pos.side)
    exit_price = current_price or pos.entry_price  # fallback to entry if unavailable

    gross_pnl = (exit_price - pos.entry_price) * pos.shares
    fees = pos.size_usd * 0.02  # Approximate 2% Polymarket fee
    net_pnl = gross_pnl - fees

    pos.status = "closed"
    pos.closed_at = now
    pos.exit_price = round(exit_price, 6)
    pos.exit_reason = "manual"
    pos.gross_pnl = round(gross_pnl, 4)
    pos.fees = round(fees, 4)
    pos.net_pnl = round(net_pnl, 4)

    await db.flush()
    return MessageResponse(
        message=f"Position {position_id} closed manually. Net P&L: ${net_pnl:.2f}"
    )
