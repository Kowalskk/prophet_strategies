"""
Performance routes.

GET /performance/summary      → overall stats
GET /performance/history      → daily P&L time series (30 days)
GET /performance/by-strategy  → breakdown by strategy
GET /performance/by-crypto    → breakdown by crypto
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import (
    PerformanceSummaryResponse,
    PnLPointResponse,
    StrategyBreakdownResponse,
)
from prophet.core.position_tracker import PositionTracker
from prophet.db.database import get_db

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/summary", response_model=PerformanceSummaryResponse)
async def performance_summary(
    db: AsyncSession = Depends(get_db),
) -> PerformanceSummaryResponse:
    """Return overall performance statistics."""
    tracker = PositionTracker(db)
    summary = await tracker.get_performance_summary()
    return PerformanceSummaryResponse(**summary)


@router.get("/history", response_model=list[PnLPointResponse])
async def performance_history(
    db: AsyncSession = Depends(get_db),
) -> list[PnLPointResponse]:
    """Return daily P&L for the last 30 days."""
    tracker = PositionTracker(db)
    daily = await tracker.get_daily_pnl(days=30)
    return [PnLPointResponse(date=d["date"], pnl=d["pnl"]) for d in daily]


@router.get("/by-strategy", response_model=list[StrategyBreakdownResponse])
async def performance_by_strategy(
    db: AsyncSession = Depends(get_db),
) -> list[StrategyBreakdownResponse]:
    """Return P&L breakdown by strategy."""
    tracker = PositionTracker(db)
    rows = await tracker.get_pnl_by_strategy()
    return [
        StrategyBreakdownResponse(
            name=r["strategy"],
            net_pnl=r["net_pnl"],
            trades=r["trades"],
            win_rate=r["win_rate"],
        )
        for r in rows
    ]


@router.get("/by-crypto", response_model=list[StrategyBreakdownResponse])
async def performance_by_crypto(
    db: AsyncSession = Depends(get_db),
) -> list[StrategyBreakdownResponse]:
    """Return P&L breakdown by crypto (BTC / ETH / SOL)."""
    tracker = PositionTracker(db)
    rows = await tracker.get_pnl_by_crypto()
    return [
        StrategyBreakdownResponse(
            name=r["crypto"],
            net_pnl=r["net_pnl"],
            trades=r["trades"],
            win_rate=r["win_rate"],
        )
        for r in rows
    ]
