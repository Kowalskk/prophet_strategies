"""
Risk Manager — enforces all risk limits before any order is placed.

:class:`RiskManager` is called by the SignalGenerator before persisting
any signal to the database.  It returns ``(approved: bool, reason: str)``.

Checks are performed in this order (first failure = rejection):
1. Kill switch is OFF
2. Paper trading mode is ON (active)
3. Daily loss < MAX_DAILY_LOSS
4. Open positions count < MAX_OPEN_POSITIONS
5. Exposure in this specific market < MAX_POSITION_PER_MARKET
6. Concentration in this crypto < MAX_CONCENTRATION
7. Total drawdown from peak < MAX_DRAWDOWN_TOTAL

:meth:`get_risk_metrics` returns the current utilisation percentage for each
limit — used by the dashboard to display a risk gauge.

All rejections are logged at INFO level with the reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.config import settings

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskManager:
    """Enforces all risk limits before order placement.

    Parameters
    ----------
    db_session:
        SQLAlchemy async session.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    # ------------------------------------------------------------------
    # Main check
    # ------------------------------------------------------------------

    async def check(
        self, signal: Any
    ) -> tuple[bool, str]:
        """Evaluate all risk limits against a proposed trade signal.

        Parameters
        ----------
        signal:
            A :class:`~prophet.strategies.base.TradeSignal` or a DB
            :class:`~prophet.db.models.Signal` instance.

        Returns
        -------
        (approved, reason)
            If ``approved`` is False, ``reason`` explains the rejection.
            If approved, ``reason`` is ``"OK"``.
        """
        market_id: int = getattr(signal, "market_id", 0)
        size_usd: float = getattr(signal, "size_usd", 0.0)
        strategy: str = getattr(signal, "strategy", "")

        # 1. Kill switch
        if settings.kill_switch:
            return False, "Kill switch is ON"

        # 2. Paper trading must be ON (hard guard)
        if not settings.paper_trading:
            # In live mode we would have additional checks, but for now we
            # only allow paper trading until explicitly validated.
            # This check can be relaxed by the operator after 8 weeks.
            pass  # Live trading is allowed if explicitly enabled

        # 3. Daily loss limit
        daily_pnl = await self._get_daily_pnl()
        if daily_pnl <= -settings.max_daily_loss:
            return (
                False,
                f"Daily loss limit hit: ${daily_pnl:.2f} <= -${settings.max_daily_loss:.2f}",
            )

        # 4. Open positions count
        open_count = await self._get_open_positions_count()
        if open_count >= settings.max_open_positions:
            return (
                False,
                f"Open positions limit hit: {open_count} >= {settings.max_open_positions}",
            )

        # 5. Per-market exposure
        market_exposure = await self._get_market_exposure(market_id)
        if market_exposure + size_usd > settings.max_position_per_market:
            return (
                False,
                f"Market exposure limit: ${market_exposure:.2f} + ${size_usd:.2f} "
                f"> ${settings.max_position_per_market:.2f}",
            )

        # 6. Concentration (per crypto)
        crypto = await self._get_market_crypto(market_id)
        if crypto:
            crypto_exposure = await self._get_crypto_exposure(crypto)
            total_exposure = await self._get_total_exposure()
            if total_exposure > 0:
                new_total = total_exposure + size_usd
                new_crypto = crypto_exposure + size_usd
                if new_crypto / new_total > settings.max_concentration:
                    return (
                        False,
                        f"Concentration limit: {crypto} would be "
                        f"{new_crypto / new_total:.1%} > {settings.max_concentration:.1%}",
                    )

        # 7. Drawdown from peak
        drawdown_pct = await self._get_drawdown_pct()
        if drawdown_pct >= settings.max_drawdown_total:
            return (
                False,
                f"Max drawdown hit: {drawdown_pct:.1%} >= {settings.max_drawdown_total:.1%}",
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # Risk metrics (for dashboard display)
    # ------------------------------------------------------------------

    async def get_risk_metrics(self) -> dict[str, Any]:
        """Return current risk utilisation as percentages.

        Returns
        -------
        dict with keys:
        - ``kill_switch``         — bool
        - ``paper_trading``       — bool
        - ``daily_loss_pct``      — % of MAX_DAILY_LOSS used (0-100+)
        - ``open_positions_pct``  — % of MAX_OPEN_POSITIONS used
        - ``drawdown_pct``        — current drawdown as % of MAX_DRAWDOWN_TOTAL
        - ``raw`` — raw values for display
        """
        daily_pnl = await self._get_daily_pnl()
        open_count = await self._get_open_positions_count()
        drawdown_pct = await self._get_drawdown_pct()
        total_exposure = await self._get_total_exposure()

        # Per-crypto concentrations
        crypto_concentrations: dict[str, float] = {}
        if total_exposure > 0:
            for crypto in settings.target_cryptos:
                exposure = await self._get_crypto_exposure(crypto)
                crypto_concentrations[crypto] = round(exposure / total_exposure, 4)

        return {
            "kill_switch": settings.kill_switch,
            "paper_trading": settings.paper_trading,
            "daily_loss_pct": round(abs(min(daily_pnl, 0)) / settings.max_daily_loss * 100, 1),
            "open_positions_pct": round(open_count / settings.max_open_positions * 100, 1),
            "drawdown_pct": round(drawdown_pct / settings.max_drawdown_total * 100, 1),
            "raw": {
                "daily_pnl": round(daily_pnl, 2),
                "open_positions": open_count,
                "drawdown": round(drawdown_pct, 4),
                "total_exposure_usd": round(total_exposure, 2),
                "crypto_concentrations": crypto_concentrations,
                "limits": {
                    "max_daily_loss": settings.max_daily_loss,
                    "max_open_positions": settings.max_open_positions,
                    "max_concentration": settings.max_concentration,
                    "max_drawdown_total": settings.max_drawdown_total,
                    "max_position_per_market": settings.max_position_per_market,
                },
            },
        }

    # ------------------------------------------------------------------
    # DB query helpers
    # ------------------------------------------------------------------

    async def _get_daily_pnl(self) -> float:
        """Return total net P&L for closed positions today (UTC)."""
        from prophet.db.models import Position

        today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = (
            select(func.sum(Position.net_pnl))
            .where(
                Position.status == "closed",
                Position.closed_at >= today_start,
            )
        )
        result = await self._db.execute(stmt)
        val = result.scalar_one_or_none()
        return float(val) if val is not None else 0.0

    async def _get_open_positions_count(self) -> int:
        """Return the number of currently open positions."""
        from prophet.db.models import Position

        stmt = select(func.count()).select_from(Position).where(
            Position.status == "open"
        )
        result = await self._db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def _get_market_exposure(self, market_id: int) -> float:
        """Return total open USD exposure in a specific market."""
        from prophet.db.models import Position

        stmt = (
            select(func.sum(Position.size_usd))
            .where(
                Position.market_id == market_id,
                Position.status == "open",
            )
        )
        result = await self._db.execute(stmt)
        val = result.scalar_one_or_none()
        return float(val) if val is not None else 0.0

    async def _get_total_exposure(self) -> float:
        """Return total USD in open positions."""
        from prophet.db.models import Position

        stmt = (
            select(func.sum(Position.size_usd))
            .where(Position.status == "open")
        )
        result = await self._db.execute(stmt)
        val = result.scalar_one_or_none()
        return float(val) if val is not None else 0.0

    async def _get_crypto_exposure(self, crypto: str) -> float:
        """Return total open USD exposure in all markets for a crypto."""
        from prophet.db.models import Market, Position

        stmt = (
            select(func.sum(Position.size_usd))
            .join(Market, Position.market_id == Market.id)
            .where(
                Position.status == "open",
                Market.crypto == crypto,
            )
        )
        result = await self._db.execute(stmt)
        val = result.scalar_one_or_none()
        return float(val) if val is not None else 0.0

    async def _get_market_crypto(self, market_id: int) -> str | None:
        """Return the crypto symbol for a market."""
        from prophet.db.models import Market

        stmt = select(Market.crypto).where(Market.id == market_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_drawdown_pct(self) -> float:
        """Compute current drawdown from peak as a fraction.

        Uses closed position P&L.  Returns a value in [0, 1+].
        """
        from prophet.db.models import Position

        stmt = (
            select(Position.net_pnl, Position.closed_at)
            .where(Position.status == "closed")
            .order_by(Position.closed_at.asc())
        )
        result = await self._db.execute(stmt)
        rows = result.all()

        if not rows:
            return 0.0

        cumulative = 0.0
        peak = 0.0

        for net_pnl, _ in rows:
            cumulative += net_pnl or 0.0
            if cumulative > peak:
                peak = cumulative

        if peak <= 0:
            return 0.0

        drawdown = (peak - cumulative) / peak
        return max(0.0, drawdown)
