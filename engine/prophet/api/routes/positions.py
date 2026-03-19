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


async def _get_current_price(db: AsyncSession, market_id: int, side: str) -> float | None:
    """Fetch the latest best_bid from the most recent orderbook snapshot."""
    try:
        from prophet.db.models import Market, OrderBookSnapshot

        # Determine the correct token_id for this side
        market = await db.get(Market, market_id)
        if market is None:
            return None
        token_id = market.token_id_yes if side.upper() == "YES" else market.token_id_no

        from sqlalchemy import select as sa_select

        stmt = (
            sa_select(OrderBookSnapshot.best_bid)
            .where(
                OrderBookSnapshot.market_id == market_id,
                OrderBookSnapshot.token_id == token_id,
            )
            .order_by(OrderBookSnapshot.timestamp.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        return float(row) if row is not None else None
    except Exception:
        logger.debug("_get_current_price failed for market_id=%s side=%s", market_id, side, exc_info=True)
        return None


@router.get("", response_model=PositionListResponse)
async def list_open_positions(
    db: AsyncSession = Depends(get_db),
) -> PositionListResponse:
    """Return all open positions with live unrealized P&L estimates."""
    from prophet.db.models import Market, OrderBookSnapshot, Position

    stmt = (
        select(Position)
        .where(Position.status == "open")
        .order_by(Position.opened_at.desc())
    )
    result = await db.execute(stmt)
    positions = list(result.scalars().all())

    if not positions:
        return PositionListResponse(items=[], total=0)

    # Fetch all latest OB snapshots in one query (one row per market+side combo)
    # using a lateral/subquery approach: get distinct market_ids, then one query.
    market_ids = list({p.market_id for p in positions})

    # Get all markets to resolve token_ids
    markets_stmt = select(Market).where(Market.id.in_(market_ids))
    markets_result = await db.execute(markets_stmt)
    markets_map = {m.id: m for m in markets_result.scalars().all()}

    # Build token_id → market_id+side lookup
    token_to_key: dict[str, tuple[int, str]] = {}
    for m in markets_map.values():
        if m.token_id_yes:
            token_to_key[m.token_id_yes] = (m.id, "YES")
        if m.token_id_no:
            token_to_key[m.token_id_no] = (m.id, "NO")

    token_ids = list(token_to_key.keys())

    # Single query: latest snapshot per token_id using ROW_NUMBER
    from sqlalchemy import func, over
    from sqlalchemy.orm import aliased

    if token_ids:
        # Subquery with row_number to get latest per token
        rn = func.row_number().over(
            partition_by=OrderBookSnapshot.token_id,
            order_by=OrderBookSnapshot.timestamp.desc(),
        ).label("rn")
        sub = (
            select(OrderBookSnapshot, rn)
            .where(OrderBookSnapshot.token_id.in_(token_ids))
            .subquery()
        )
        latest_stmt = select(sub).where(sub.c.rn == 1)
        latest_result = await db.execute(latest_stmt)
        rows = latest_result.fetchall()

        # Build (market_id, side) → best_bid lookup
        price_map: dict[tuple[int, str], float] = {}
        for row in rows:
            token_id = row.token_id
            best_bid = row.best_bid
            if token_id in token_to_key and best_bid is not None:
                key = token_to_key[token_id]
                price_map[key] = float(best_bid)
    else:
        price_map = {}

    items = []
    for pos in positions:
        current_price = price_map.get((pos.market_id, pos.side.upper()))
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
    current_price = await _get_current_price(db, pos.market_id, pos.side)
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
