"""
Position Tracker — queries and aggregates position and P&L data.

:class:`PositionTracker` provides read-only analytics on top of the
``positions`` table.  It is used by:
- The REST API (``/positions``, ``/performance/summary``, etc.)
- The dashboard for charts and stats

All methods are async and use SQLAlchemy queries.  No data is written here.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PositionTracker:
    """Read-only analytics over open and closed positions.

    Parameters
    ----------
    db_session:
        SQLAlchemy async session.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    # ------------------------------------------------------------------
    # Basic position queries
    # ------------------------------------------------------------------

    async def get_open_positions(self) -> list[Any]:
        """Return all currently open positions."""
        from prophet.db.models import Position

        stmt = (
            select(Position)
            .where(Position.status == "open")
            .order_by(Position.opened_at.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_closed_positions(
        self, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        """Return closed positions with pagination.

        Parameters
        ----------
        limit:
            Maximum rows to return.
        offset:
            Number of rows to skip (for pagination).
        """
        from prophet.db.models import Position

        stmt = (
            select(Position)
            .where(Position.status == "closed")
            .order_by(Position.closed_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Performance summary
    # ------------------------------------------------------------------

    async def get_performance_summary(self) -> dict[str, Any]:
        """Return overall performance statistics.

        Returns
        -------
        dict with keys:
        - ``total_pnl``      — cumulative net P&L (USD)
        - ``win_rate``       — fraction of closed trades with net_pnl > 0
        - ``sharpe_ratio``   — annualised Sharpe ratio (using daily P&L)
        - ``profit_factor``  — gross profits / gross losses
        - ``max_drawdown``   — maximum peak-to-trough drawdown (USD)
        - ``total_trades``   — total number of closed positions
        - ``open_positions`` — number of currently open positions
        """
        from prophet.db.models import Position

        # Closed positions
        stmt_closed = select(Position).where(Position.status == "closed")
        result = await self._db.execute(stmt_closed)
        closed = list(result.scalars().all())

        # Open positions count
        stmt_open = select(func.count()).select_from(Position).where(
            Position.status == "open"
        )
        open_count = (await self._db.execute(stmt_open)).scalar_one() or 0

        total_trades = len(closed)
        total_pnl = sum(p.net_pnl or 0.0 for p in closed)

        wins = [p for p in closed if (p.net_pnl or 0.0) > 0]
        losses = [p for p in closed if (p.net_pnl or 0.0) < 0]

        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        gross_profit = sum(p.net_pnl for p in wins if p.net_pnl is not None)
        gross_loss = abs(sum(p.net_pnl for p in losses if p.net_pnl is not None))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        # Sharpe ratio (annualised, using daily returns)
        daily_pnl = await self.get_daily_pnl(days=90)
        sharpe = _compute_sharpe(daily_pnl)

        # Maximum drawdown
        max_drawdown = _compute_max_drawdown(daily_pnl)

        return {
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 4),
            "sharpe_ratio": round(sharpe, 4),
            "profit_factor": round(profit_factor, 4),
            "max_drawdown": round(max_drawdown, 2),
            "total_trades": total_trades,
            "open_positions": open_count,
        }

    # ------------------------------------------------------------------
    # Time series
    # ------------------------------------------------------------------

    async def get_daily_pnl(self, days: int = 30) -> list[dict[str, Any]]:
        """Return daily P&L for the last ``days`` days.

        Returns
        -------
        list of ``{"date": "YYYY-MM-DD", "pnl": float}`` dicts, ordered
        by date ascending.
        """
        from prophet.db.models import Position

        since = _utcnow() - timedelta(days=days)
        stmt = (
            select(Position)
            .where(
                Position.status == "closed",
                Position.closed_at >= since,
            )
            .order_by(Position.closed_at.asc())
        )
        result = await self._db.execute(stmt)
        positions = list(result.scalars().all())

        # Group by date
        daily: dict[str, float] = {}
        for pos in positions:
            if pos.closed_at is None:
                continue
            closed = pos.closed_at
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=timezone.utc)
            date_str = closed.date().isoformat()
            daily[date_str] = daily.get(date_str, 0.0) + (pos.net_pnl or 0.0)

        # Fill in missing days with 0
        result_list: list[dict[str, Any]] = []
        for i in range(days):
            d = (_utcnow() - timedelta(days=days - 1 - i)).date()
            date_str = d.isoformat()
            result_list.append({"date": date_str, "pnl": round(daily.get(date_str, 0.0), 4)})

        return result_list

    # ------------------------------------------------------------------
    # Breakdowns
    # ------------------------------------------------------------------

    async def get_pnl_by_strategy(self) -> list[dict[str, Any]]:
        """Return P&L breakdown by strategy.

        Returns
        -------
        list of ``{"strategy": str, "net_pnl": float, "trades": int,
        "win_rate": float}`` dicts.
        """
        from prophet.db.models import Position

        stmt = (
            select(Position)
            .where(Position.status == "closed")
        )
        result = await self._db.execute(stmt)
        positions = list(result.scalars().all())

        buckets: dict[str, list[float]] = {}
        for pos in positions:
            key = pos.strategy or "unknown"
            buckets.setdefault(key, []).append(pos.net_pnl or 0.0)

        rows = []
        for strategy, pnls in sorted(buckets.items()):
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "strategy": strategy,
                "net_pnl": round(sum(pnls), 2),
                "trades": len(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            })

        return rows

    async def get_pnl_by_crypto(self) -> list[dict[str, Any]]:
        """Return P&L breakdown by crypto (BTC / ETH / SOL).

        Joins positions → markets to get the crypto field.

        Returns
        -------
        list of ``{"crypto": str, "net_pnl": float, "trades": int,
        "win_rate": float}`` dicts.
        """
        from prophet.db.models import Market, Position

        stmt = (
            select(Position, Market.crypto)
            .join(Market, Position.market_id == Market.id)
            .where(Position.status == "closed")
        )
        result = await self._db.execute(stmt)
        rows_raw = result.all()  # list of (Position, crypto)

        buckets: dict[str, list[float]] = {}
        for pos, crypto in rows_raw:
            key = crypto or "unknown"
            buckets.setdefault(key, []).append(pos.net_pnl or 0.0)

        rows = []
        for crypto, pnls in sorted(buckets.items()):
            wins = sum(1 for p in pnls if p > 0)
            rows.append({
                "crypto": crypto,
                "net_pnl": round(sum(pnls), 2),
                "trades": len(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            })

        return rows


# ---------------------------------------------------------------------------
# Pure math helpers (no I/O)
# ---------------------------------------------------------------------------


def _compute_sharpe(daily_pnl: list[dict[str, Any]]) -> float:
    """Compute annualised Sharpe ratio from daily P&L list.

    Uses risk-free rate = 0 (simplification appropriate for short-duration
    prediction market positions).
    """
    pnls = [d["pnl"] for d in daily_pnl]
    n = len(pnls)
    if n < 2:
        return 0.0

    mean = sum(pnls) / n
    variance = sum((x - mean) ** 2 for x in pnls) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0.0:
        return 0.0

    # Annualise: multiply by sqrt(252) trading days
    sharpe = (mean / std) * math.sqrt(252)
    return sharpe


def _compute_max_drawdown(daily_pnl: list[dict[str, Any]]) -> float:
    """Compute the maximum peak-to-trough drawdown in USD."""
    pnls = [d["pnl"] for d in daily_pnl]
    if not pnls:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd
