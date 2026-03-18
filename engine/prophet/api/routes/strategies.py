"""
Strategies routes.

GET  /strategies                        → list all strategies + enabled status
PUT  /strategies/{name}/toggle          → toggle enabled flag in DB
GET  /strategies/{name}/config          → default config from DB
PUT  /strategies/{name}/config          → update default config in DB
POST /strategies/{name}/assign          → assign to specific market(s)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import MessageResponse, StrategyConfigResponse, StrategyResponse
from prophet.db.database import get_db
from prophet.strategies.registry import STRATEGY_REGISTRY, list_strategies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class UpdateConfigBody(BaseModel):
    params: dict[str, Any]


class AssignBody(BaseModel):
    market_ids: list[int]
    enabled: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[StrategyResponse])
async def list_all_strategies(
    db: AsyncSession = Depends(get_db),
) -> list[StrategyResponse]:
    """List all registered strategies with their current enabled status."""
    from prophet.db.models import StrategyConfig

    # Build a map of enabled status from the DB (default rows: market_id=None)
    enabled_map: dict[str, bool] = {}
    try:
        stmt = select(StrategyConfig).where(StrategyConfig.market_id.is_(None))
        result = await db.execute(stmt)
        rows = result.scalars().all()
        for row in rows:
            # Most specific default wins
            if row.crypto is None:
                enabled_map[row.strategy] = row.enabled
    except Exception as exc:
        logger.warning("Could not load strategy configs from DB: %s", exc)

    out = []
    for info in list_strategies():
        out.append(
            StrategyResponse(
                name=info["name"],
                description=info["description"],
                default_params=info["default_params"],
                enabled=enabled_map.get(info["name"], True),
            )
        )
    return out


@router.put("/{name}/toggle", response_model=MessageResponse)
async def toggle_strategy(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Toggle the enabled flag for a strategy's default config."""
    from prophet.db.models import StrategyConfig

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Strategy {name!r} not found.")

    stmt = select(StrategyConfig).where(
        StrategyConfig.strategy == name,
        StrategyConfig.market_id.is_(None),
        StrategyConfig.crypto.is_(None),
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        # Create default row as enabled=False (toggled from implicit True)
        row = StrategyConfig(
            strategy=name,
            market_id=None,
            crypto=None,
            enabled=False,
            params={},
        )
        db.add(row)
    else:
        row.enabled = not row.enabled

    await db.flush()
    state = "enabled" if row.enabled else "disabled"
    return MessageResponse(message=f"Strategy {name!r} is now {state}.")


@router.get("/{name}/config", response_model=StrategyConfigResponse | None)
async def get_strategy_config(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> StrategyConfigResponse | None:
    """Return the default config for a strategy (market_id=None)."""
    from prophet.db.models import StrategyConfig

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Strategy {name!r} not found.")

    stmt = select(StrategyConfig).where(
        StrategyConfig.strategy == name,
        StrategyConfig.market_id.is_(None),
        StrategyConfig.crypto.is_(None),
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return StrategyConfigResponse.model_validate(row)


@router.put("/{name}/config", response_model=StrategyConfigResponse)
async def update_strategy_config(
    name: str,
    body: UpdateConfigBody,
    db: AsyncSession = Depends(get_db),
) -> StrategyConfigResponse:
    """Update (or create) the default param config for a strategy."""
    from prophet.db.models import StrategyConfig

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Strategy {name!r} not found.")

    stmt = select(StrategyConfig).where(
        StrategyConfig.strategy == name,
        StrategyConfig.market_id.is_(None),
        StrategyConfig.crypto.is_(None),
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = StrategyConfig(
            strategy=name,
            market_id=None,
            crypto=None,
            enabled=True,
            params=body.params,
        )
        db.add(row)
    else:
        row.params = body.params

    await db.flush()
    await db.refresh(row)
    return StrategyConfigResponse.model_validate(row)


@router.post("/{name}/assign", response_model=MessageResponse)
async def assign_strategy(
    name: str,
    body: AssignBody,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Assign/unassign a strategy to specific markets."""
    from prophet.db.models import Market, StrategyConfig

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Strategy {name!r} not found.")

    updated = 0
    for market_id in body.market_ids:
        # Verify market exists
        mkt = await db.get(Market, market_id)
        if mkt is None:
            logger.warning("assign_strategy: market_id=%d not found, skipping", market_id)
            continue

        stmt = select(StrategyConfig).where(
            StrategyConfig.strategy == name,
            StrategyConfig.market_id == market_id,
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            row = StrategyConfig(
                strategy=name,
                market_id=market_id,
                crypto=None,
                enabled=body.enabled,
                params={},
            )
            db.add(row)
        else:
            row.enabled = body.enabled

        updated += 1

    await db.flush()
    return MessageResponse(
        message=f"Strategy {name!r} updated for {updated} market(s)."
    )
