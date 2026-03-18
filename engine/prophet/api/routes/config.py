"""
Config routes.

GET /config       → return all risk limits + current settings
PUT /config       → update risk limits
GET /config/risk  → return RiskManager.get_risk_metrics()
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.api.schemas import ConfigResponse, RiskMetricsResponse
from prophet.config import settings
from prophet.core.risk_manager import RiskManager
from prophet.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["config"])


class UpdateConfigBody(BaseModel):
    max_position_per_market: float | None = None
    max_daily_loss: float | None = None
    max_open_positions: int | None = None
    max_concentration: float | None = None
    max_drawdown_total: float | None = None
    scan_interval_minutes: int | None = None


@router.get("", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Return all current risk limits and system settings."""
    return ConfigResponse(
        paper_trading=settings.paper_trading,
        kill_switch=settings.kill_switch,
        scan_interval_minutes=settings.scan_interval_minutes,
        target_cryptos=settings.target_cryptos,
        api_host=settings.api_host,
        api_port=settings.api_port,
        max_position_per_market=settings.max_position_per_market,
        max_daily_loss=settings.max_daily_loss,
        max_open_positions=settings.max_open_positions,
        max_concentration=settings.max_concentration,
        max_drawdown_total=settings.max_drawdown_total,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(body: UpdateConfigBody) -> ConfigResponse:
    """Update risk limits at runtime (in-memory only; restart resets to .env values)."""
    errors = []

    if body.max_daily_loss is not None:
        if body.max_daily_loss <= 0:
            errors.append("max_daily_loss must be > 0")
        else:
            object.__setattr__(settings, "max_daily_loss", body.max_daily_loss)

    if body.max_open_positions is not None:
        if body.max_open_positions <= 0:
            errors.append("max_open_positions must be > 0")
        else:
            object.__setattr__(settings, "max_open_positions", body.max_open_positions)

    if body.max_position_per_market is not None:
        if body.max_position_per_market <= 0:
            errors.append("max_position_per_market must be > 0")
        else:
            object.__setattr__(
                settings, "max_position_per_market", body.max_position_per_market
            )

    if body.max_concentration is not None:
        if not (0 < body.max_concentration <= 1):
            errors.append("max_concentration must be in (0, 1]")
        else:
            object.__setattr__(settings, "max_concentration", body.max_concentration)

    if body.max_drawdown_total is not None:
        if not (0 < body.max_drawdown_total <= 1):
            errors.append("max_drawdown_total must be in (0, 1]")
        else:
            object.__setattr__(settings, "max_drawdown_total", body.max_drawdown_total)

    if body.scan_interval_minutes is not None:
        if body.scan_interval_minutes <= 0:
            errors.append("scan_interval_minutes must be > 0")
        else:
            object.__setattr__(
                settings, "scan_interval_minutes", body.scan_interval_minutes
            )

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    return ConfigResponse(
        paper_trading=settings.paper_trading,
        kill_switch=settings.kill_switch,
        scan_interval_minutes=settings.scan_interval_minutes,
        target_cryptos=settings.target_cryptos,
        api_host=settings.api_host,
        api_port=settings.api_port,
        max_position_per_market=settings.max_position_per_market,
        max_daily_loss=settings.max_daily_loss,
        max_open_positions=settings.max_open_positions,
        max_concentration=settings.max_concentration,
        max_drawdown_total=settings.max_drawdown_total,
    )


@router.get("/risk", response_model=RiskMetricsResponse)
async def get_risk_metrics(db: AsyncSession = Depends(get_db)) -> RiskMetricsResponse:
    """Return current risk utilisation percentages."""
    risk_manager = RiskManager(db)
    metrics = await risk_manager.get_risk_metrics()
    return RiskMetricsResponse(**metrics)
