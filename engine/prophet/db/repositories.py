"""
Data Access Layer — one Repository class per main table.

All repositories use SQLAlchemy 2.0 async select() patterns and accept an
:class:`~sqlalchemy.ext.asyncio.AsyncSession` as their first argument.

Usage example::

    from prophet.db.repositories import MarketRepository

    async with get_session() as db:
        markets = await MarketRepository.get_active_markets(db)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.db.models import (
    Market,
    OrderBookSnapshot,
    PaperOrder,
    Position,
    PriceSnapshot,
    Signal,
    StrategyConfig,
    SystemState,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# MarketRepository
# ---------------------------------------------------------------------------


class MarketRepository:
    """CRUD operations for the ``markets`` table."""

    @staticmethod
    async def get_active_markets(db: AsyncSession) -> list[Market]:
        """Return all markets with status='active'."""
        stmt = select(Market).where(Market.status == "active").order_by(Market.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_condition_id(db: AsyncSession, condition_id: str) -> Market | None:
        """Return a single market matching the condition_id, or None."""
        stmt = select(Market).where(Market.condition_id == condition_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_crypto(
        db: AsyncSession, crypto: str, status: str = "active"
    ) -> list[Market]:
        """Return markets filtered by crypto symbol and status."""
        stmt = (
            select(Market)
            .where(Market.crypto == crypto, Market.status == status)
            .order_by(Market.resolution_date.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def upsert(db: AsyncSession, data: dict[str, Any]) -> Market:
        """Insert or update a market row by condition_id.

        If a market with the given ``condition_id`` already exists, all fields
        in *data* are updated.  Otherwise a new row is created.

        Returns the persisted :class:`Market` instance.
        """
        condition_id = data.get("condition_id")
        if not condition_id:
            raise ValueError("data must contain 'condition_id'")

        existing = await MarketRepository.get_by_condition_id(db, condition_id)
        if existing:
            for key, value in data.items():
                if key != "id" and hasattr(existing, key):
                    setattr(existing, key, value)
            await db.flush()
            logger.debug("Market upserted (updated): condition_id=%s", condition_id)
            return existing

        market = Market(**{k: v for k, v in data.items() if k != "id"})
        db.add(market)
        await db.flush()
        logger.debug("Market upserted (created): condition_id=%s", condition_id)
        return market

    @staticmethod
    async def mark_resolved(
        db: AsyncSession,
        condition_id: str,
        outcome: str,
        resolution_time: datetime,
    ) -> Market:
        """Set a market as resolved with the given outcome and timestamp."""
        market = await MarketRepository.get_by_condition_id(db, condition_id)
        if market is None:
            raise NoResultFound(f"Market not found: condition_id={condition_id!r}")
        market.status = "resolved"
        market.resolved_outcome = outcome.upper()
        market.resolution_time = resolution_time
        await db.flush()
        logger.info("Market resolved: condition_id=%s outcome=%s", condition_id, outcome)
        return market


# ---------------------------------------------------------------------------
# SignalRepository
# ---------------------------------------------------------------------------


class SignalRepository:
    """CRUD operations for the ``signals`` table."""

    @staticmethod
    async def create(db: AsyncSession, signal: Signal) -> Signal:
        """Persist a new Signal and return it with its auto-assigned id."""
        db.add(signal)
        await db.flush()
        logger.debug(
            "Signal created: id=%s strategy=%s side=%s price=%s",
            signal.id,
            signal.strategy,
            signal.side,
            signal.target_price,
        )
        return signal

    @staticmethod
    async def get_pending(db: AsyncSession) -> list[Signal]:
        """Return all signals with status='pending', oldest first."""
        stmt = (
            select(Signal)
            .where(Signal.status == "pending")
            .order_by(Signal.created_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_market(
        db: AsyncSession, market_id: int, limit: int = 50
    ) -> list[Signal]:
        """Return recent signals for a specific market, newest first."""
        stmt = (
            select(Signal)
            .where(Signal.market_id == market_id)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def expire_old(db: AsyncSession, cutoff: datetime) -> int:
        """Set all pending signals older than *cutoff* to status='expired'.

        Returns the number of signals expired.
        """
        stmt = (
            update(Signal)
            .where(Signal.status == "pending", Signal.created_at < cutoff)
            .values(status="expired")
            .returning(Signal.id)
        )
        result = await db.execute(stmt)
        expired = list(result.scalars().all())
        count = len(expired)
        if count:
            logger.info("Expired %d old signals (cutoff=%s)", count, cutoff)
        return count


# ---------------------------------------------------------------------------
# PaperOrderRepository
# ---------------------------------------------------------------------------


class PaperOrderRepository:
    """CRUD operations for the ``paper_orders`` table."""

    @staticmethod
    async def create(db: AsyncSession, order: PaperOrder) -> PaperOrder:
        """Persist a new PaperOrder and return it with its id."""
        db.add(order)
        await db.flush()
        logger.debug(
            "PaperOrder created: id=%s strategy=%s side=%s price=%s",
            order.id,
            order.strategy,
            order.side,
            order.target_price,
        )
        return order

    @staticmethod
    async def get_open(db: AsyncSession) -> list[PaperOrder]:
        """Return all open paper orders, oldest first."""
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.status == "open")
            .order_by(PaperOrder.placed_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_market(db: AsyncSession, market_id: int) -> list[PaperOrder]:
        """Return all paper orders for a specific market."""
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.market_id == market_id)
            .order_by(PaperOrder.placed_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_status(
        db: AsyncSession,
        order_id: int,
        status: str,
        **kwargs: Any,
    ) -> PaperOrder:
        """Update an order's status and any additional fields passed as kwargs.

        Commonly used kwargs: ``fill_price``, ``fill_size_usd``, ``filled_at``,
        ``cancel_reason``.
        """
        stmt = select(PaperOrder).where(PaperOrder.id == order_id)
        result = await db.execute(stmt)
        order = result.scalar_one_or_none()
        if order is None:
            raise NoResultFound(f"PaperOrder not found: id={order_id}")

        order.status = status
        for key, value in kwargs.items():
            if hasattr(order, key):
                setattr(order, key, value)
        await db.flush()
        logger.debug("PaperOrder %d status → %s", order_id, status)
        return order

    @staticmethod
    async def get_fills_in_range(
        db: AsyncSession, start: datetime, end: datetime
    ) -> list[PaperOrder]:
        """Return filled orders whose fill timestamp falls in [start, end]."""
        stmt = (
            select(PaperOrder)
            .where(
                PaperOrder.status == "filled",
                PaperOrder.filled_at >= start,
                PaperOrder.filled_at <= end,
            )
            .order_by(PaperOrder.filled_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PositionRepository
# ---------------------------------------------------------------------------


class PositionRepository:
    """CRUD operations for the ``positions`` table."""

    @staticmethod
    async def create(db: AsyncSession, pos: Position) -> Position:
        """Persist a new Position and return it with its id."""
        db.add(pos)
        await db.flush()
        logger.debug(
            "Position created: id=%s strategy=%s side=%s entry=%s",
            pos.id,
            pos.strategy,
            pos.side,
            pos.entry_price,
        )
        return pos

    @staticmethod
    async def get_open(db: AsyncSession) -> list[Position]:
        """Return all open positions, newest first."""
        stmt = (
            select(Position)
            .where(Position.status == "open")
            .order_by(Position.opened_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_closed(
        db: AsyncSession, limit: int = 50, offset: int = 0
    ) -> list[Position]:
        """Return closed positions with pagination, newest first."""
        stmt = (
            select(Position)
            .where(Position.status == "closed")
            .order_by(Position.closed_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_strategy(db: AsyncSession, strategy: str) -> list[Position]:
        """Return all positions (open or closed) for a given strategy."""
        stmt = (
            select(Position)
            .where(Position.strategy == strategy)
            .order_by(Position.opened_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_daily_pnl(
        db: AsyncSession, days: int = 30
    ) -> list[dict[str, Any]]:
        """Return daily net P&L for the last *days* days.

        Returns a list of ``{"date": "YYYY-MM-DD", "pnl": float}`` dicts,
        ordered by date ascending, with zero-filled gaps.
        """
        from datetime import timedelta

        since = _utcnow() - timedelta(days=days)
        stmt = (
            select(Position)
            .where(
                Position.status == "closed",
                Position.closed_at >= since,
            )
            .order_by(Position.closed_at.asc())
        )
        result = await db.execute(stmt)
        positions = list(result.scalars().all())

        daily: dict[str, float] = {}
        for pos in positions:
            if pos.closed_at is None:
                continue
            dt = pos.closed_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_str = dt.date().isoformat()
            daily[date_str] = daily.get(date_str, 0.0) + (pos.net_pnl or 0.0)

        result_list: list[dict[str, Any]] = []
        for i in range(days):
            d = (_utcnow() - timedelta(days=days - 1 - i)).date()
            date_str = d.isoformat()
            result_list.append({"date": date_str, "pnl": round(daily.get(date_str, 0.0), 4)})

        return result_list

    @staticmethod
    async def close(
        db: AsyncSession,
        position_id: int,
        exit_price: float,
        exit_reason: str,
    ) -> Position:
        """Close a position: set status, exit_price, exit_reason, gross/net P&L."""
        stmt = select(Position).where(Position.id == position_id)
        result = await db.execute(stmt)
        pos = result.scalar_one_or_none()
        if pos is None:
            raise NoResultFound(f"Position not found: id={position_id}")

        pos.status = "closed"
        pos.exit_price = exit_price
        pos.exit_reason = exit_reason
        pos.closed_at = _utcnow()

        # P&L calculation
        gross_pnl = (exit_price - pos.entry_price) * pos.shares
        # Polymarket taker fee ~2% on notional (shares * exit_price)
        fees = pos.shares * exit_price * 0.02
        pos.gross_pnl = round(gross_pnl, 6)
        pos.fees = round(fees, 6)
        pos.net_pnl = round(gross_pnl - fees, 6)

        await db.flush()
        logger.info(
            "Position %d closed: exit_price=%s net_pnl=%s reason=%s",
            position_id,
            exit_price,
            pos.net_pnl,
            exit_reason,
        )
        return pos


# ---------------------------------------------------------------------------
# OrderBookRepository
# ---------------------------------------------------------------------------


class OrderBookRepository:
    """CRUD operations for the ``orderbook_snapshots`` table."""

    @staticmethod
    async def save_snapshot(
        db: AsyncSession, snapshot: OrderBookSnapshot
    ) -> OrderBookSnapshot:
        """Persist an order book snapshot and return it with its id."""
        db.add(snapshot)
        await db.flush()
        return snapshot

    @staticmethod
    async def get_latest(
        db: AsyncSession, market_id: int, side: str
    ) -> OrderBookSnapshot | None:
        """Return the most recent snapshot for a market+side combination."""
        stmt = (
            select(OrderBookSnapshot)
            .where(
                OrderBookSnapshot.market_id == market_id,
                OrderBookSnapshot.side == side,
            )
            .order_by(OrderBookSnapshot.timestamp.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_history(
        db: AsyncSession, market_id: int, limit: int = 100
    ) -> list[OrderBookSnapshot]:
        """Return recent snapshots for a market, newest first."""
        stmt = (
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.market_id == market_id)
            .order_by(OrderBookSnapshot.timestamp.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PriceRepository
# ---------------------------------------------------------------------------


class PriceRepository:
    """CRUD operations for the ``price_snapshots`` table."""

    @staticmethod
    async def save(db: AsyncSession, snapshot: PriceSnapshot) -> PriceSnapshot:
        """Persist a price snapshot and return it with its id."""
        db.add(snapshot)
        await db.flush()
        return snapshot

    @staticmethod
    async def get_latest(db: AsyncSession, symbol: str) -> PriceSnapshot | None:
        """Return the most recent price snapshot for a crypto symbol."""
        stmt = (
            select(PriceSnapshot)
            .where(PriceSnapshot.crypto == symbol)
            .order_by(PriceSnapshot.timestamp.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_history(
        db: AsyncSession, symbol: str, limit: int = 60
    ) -> list[PriceSnapshot]:
        """Return recent price snapshots for a crypto, newest first."""
        stmt = (
            select(PriceSnapshot)
            .where(PriceSnapshot.crypto == symbol)
            .order_by(PriceSnapshot.timestamp.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# StrategyConfigRepository
# ---------------------------------------------------------------------------


class StrategyConfigRepository:
    """CRUD operations for the ``strategy_configs`` table."""

    @staticmethod
    async def get_for_market(
        db: AsyncSession, market_id: int, strategy: str
    ) -> StrategyConfig | None:
        """Return the most specific config for a market+strategy pair."""
        stmt = (
            select(StrategyConfig)
            .where(
                StrategyConfig.strategy == strategy,
                StrategyConfig.market_id == market_id,
            )
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_global_default(
        db: AsyncSession, strategy: str
    ) -> StrategyConfig | None:
        """Return the global default config (market_id=None) for a strategy."""
        stmt = (
            select(StrategyConfig)
            .where(
                StrategyConfig.strategy == strategy,
                StrategyConfig.market_id.is_(None),
            )
            .order_by(StrategyConfig.created_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        market_id: int | None,
        strategy: str,
        params: dict[str, Any],
        enabled: bool,
    ) -> StrategyConfig:
        """Insert or update a strategy config by (strategy, market_id) key."""
        # Try to find existing
        stmt = select(StrategyConfig).where(
            StrategyConfig.strategy == strategy,
            StrategyConfig.market_id == market_id
            if market_id is not None
            else StrategyConfig.market_id.is_(None),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.params = params
            existing.enabled = enabled
            await db.flush()
            return existing

        config = StrategyConfig(
            strategy=strategy,
            market_id=market_id,
            params=params,
            enabled=enabled,
        )
        db.add(config)
        await db.flush()
        logger.debug(
            "StrategyConfig created: strategy=%s market_id=%s", strategy, market_id
        )
        return config

    @staticmethod
    async def get_all_enabled(db: AsyncSession) -> list[StrategyConfig]:
        """Return all enabled strategy configs."""
        stmt = (
            select(StrategyConfig)
            .where(StrategyConfig.enabled.is_(True))
            .order_by(StrategyConfig.strategy.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# SystemStateRepository
# ---------------------------------------------------------------------------


class SystemStateRepository:
    """Key/value store backed by the ``system_state`` table.

    Values are stored as JSONB in the DB but exposed as plain strings
    through this interface (wrapping/unwrapping transparently).
    """

    @staticmethod
    async def get(db: AsyncSession, key: str) -> str | None:
        """Return the string value for *key*, or None if not set."""
        stmt = select(SystemState).where(SystemState.key == key)
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        # Value stored as {"v": "<string>"} for simplicity
        val = row.value
        if isinstance(val, dict) and "v" in val:
            return str(val["v"])
        # Fallback: stringify whatever is stored
        return str(val)

    @staticmethod
    async def set(db: AsyncSession, key: str, value: str) -> None:
        """Store *value* (as a string) under *key*, upserting if exists."""
        stmt = select(SystemState).where(SystemState.key == key)
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        payload: dict[str, Any] = {"v": value}
        if row:
            row.value = payload
        else:
            row = SystemState(key=key, value=payload)
            db.add(row)
        await db.flush()

    @staticmethod
    async def get_all(db: AsyncSession) -> dict[str, str]:
        """Return all key/value pairs as a plain string dict."""
        stmt = select(SystemState)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        out: dict[str, str] = {}
        for row in rows:
            val = row.value
            if isinstance(val, dict) and "v" in val:
                out[row.key] = str(val["v"])
            else:
                out[row.key] = str(val)
        return out
