"""
System routes — health, status, kill-switch.

GET  /health        → HealthResponse
GET  /status        → SystemStatusResponse
POST /kill-switch   → toggle kill_switch, return new state
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import HealthResponse, MessageResponse, SystemStatusResponse
from prophet.config import settings
from prophet.db.database import get_db

router = APIRouter(tags=["system"])

# Module-level start time for uptime calculation
_START_TIME = time.monotonic()
_VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — no auth required."""
    return HealthResponse(
        status="ok",
        version=_VERSION,
        uptime_seconds=round(time.monotonic() - _START_TIME, 1),
        paper_trading=settings.paper_trading,
    )


@router.get("/status", response_model=SystemStatusResponse)
async def status(db: AsyncSession = Depends(get_db)) -> SystemStatusResponse:
    """Return current engine runtime status."""
    from prophet.db.models import Position, SystemState

    # Last scan timestamp from system_state
    last_scan_at: datetime | None = None
    scanning_active = False
    try:
        stmt = select(SystemState).where(SystemState.key == "last_scan_at")
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row and row.value:
            ts_str = row.value.get("timestamp")
            if ts_str:
                last_scan_at = datetime.fromisoformat(ts_str)
                # Consider "active" if scanned within last 20 minutes
                now = datetime.now(timezone.utc)
                if last_scan_at.tzinfo is None:
                    last_scan_at = last_scan_at.replace(tzinfo=timezone.utc)
                diff = (now - last_scan_at).total_seconds()
                scanning_active = diff < 20 * 60
    except Exception:
        pass

    # Open positions count
    open_count = 0
    try:
        from sqlalchemy import func

        stmt2 = (
            select(func.count())
            .select_from(Position)
            .where(Position.status == "open")
        )
        r2 = await db.execute(stmt2)
        open_count = int(r2.scalar_one() or 0)
    except Exception:
        pass

    # Daily P&L from system_state
    daily_pnl = 0.0
    try:
        stmt3 = select(SystemState).where(SystemState.key == "daily_pnl")
        r3 = await db.execute(stmt3)
        row3 = r3.scalar_one_or_none()
        if row3 and row3.value:
            daily_pnl = float(row3.value.get("value", 0.0))
    except Exception:
        pass

    return SystemStatusResponse(
        scanning_active=scanning_active,
        last_scan_at=last_scan_at,
        open_positions=open_count,
        daily_pnl=round(daily_pnl, 2),
        kill_switch=settings.kill_switch,
    )


@router.post("/kill-switch", response_model=MessageResponse)
async def toggle_kill_switch(db: AsyncSession = Depends(get_db)) -> MessageResponse:
    """Toggle the kill switch. Persists state to the system_state table."""
    from datetime import timezone

    from prophet.db.models import SystemState

    # Toggle in-memory settings object
    # NOTE: Settings is immutable by default in pydantic-settings;
    # we use object.__setattr__ to bypass validation for runtime toggling.
    new_state = not settings.kill_switch
    object.__setattr__(settings, "kill_switch", new_state)

    # Persist to DB
    try:
        stmt = select(SystemState).where(SystemState.key == "kill_switch")
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            row = SystemState(
                key="kill_switch",
                value={"value": new_state, "toggled_at": now},
            )
            db.add(row)
        else:
            row.value = {"value": new_state, "toggled_at": now}
        await db.flush()
    except Exception:
        pass  # Don't fail the toggle if DB write fails

    state_str = "ON" if new_state else "OFF"
    return MessageResponse(message=f"Kill switch is now {state_str}.")
