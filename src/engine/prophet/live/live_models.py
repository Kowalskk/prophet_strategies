"""
SQLAlchemy models for live trading.

Separate tables from paper trading — never mixed.
  live_orders    — real CLOB orders placed
  live_positions — open/closed live positions with actual fill data
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from prophet.db.database import Base

_NOW = func.now()


def _utcnow() -> datetime:
    from datetime import timezone
    return datetime.now(timezone.utc)


class LiveOrder(Base):
    """A real limit order placed on the CLOB.

    Created when a signal is routed to live trading.
    Tracks the CLOB order ID and fill status via polling.
    """

    __tablename__ = "live_orders"
    __table_args__ = (
        Index("ix_live_orders_market_status", "market_id", "status"),
        Index("ix_live_orders_placed_at", "placed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False, comment="YES or NO.")
    token_id: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="CLOB token ID for this outcome.",
    )
    clob_order_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True,
        comment="Order ID returned by the CLOB after placement.",
    )
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    size_usd: Mapped[float] = mapped_column(Float, nullable=False)
    shares_requested: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="shares = size_usd / target_price",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True,
        comment="pending | open | filled | partially_filled | cancelled | failed.",
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW,
    )
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fill_price_actual: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="Actual average fill price from CLOB (may differ from target_price).",
    )
    fill_size_usd_actual: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="Actual USD filled (may be less than size_usd if partially filled).",
    )
    slippage_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="(fill_price_actual - target_price) / target_price * 100",
    )
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Signal params snapshot at placement time.",
    )

    # Relationship (noload — don't auto-load)
    market: Mapped[Any] = relationship("Market", lazy="noload")
    live_position: Mapped[LivePosition | None] = relationship(
        "LivePosition", back_populates="live_order", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<LiveOrder id={self.id} strategy={self.strategy!r} "
            f"side={self.side!r} price={self.target_price} status={self.status!r}>"
        )


class LivePosition(Base):
    """An open or closed live position.

    Created when a LiveOrder is confirmed filled by the CLOB.
    Tracks full lifecycle including actual fill price and slippage vs paper.
    """

    __tablename__ = "live_positions"
    __table_args__ = (
        Index("ix_live_positions_market_strategy", "market_id", "strategy"),
        Index("ix_live_positions_status", "status"),
        Index("ix_live_positions_opened_at", "opened_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    live_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("live_orders.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False, comment="YES or NO.")
    token_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Entry — actual fill data
    entry_price: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Actual fill price (from CLOB), not the target price.",
    )
    entry_price_target: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Original target price from the signal.",
    )
    size_usd: Mapped[float] = mapped_column(Float, nullable=False, comment="Capital deployed.")
    shares: Mapped[float] = mapped_column(Float, nullable=False, comment="Shares filled.")
    slippage_pct: Mapped[float | None] = mapped_column(
        Float, nullable=True,
        comment="Entry slippage vs target: (fill-target)/target * 100",
    )

    # Exit config — copied from signal.params at fill time
    exit_strategy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="hold_to_resolution",
        comment="hold_to_resolution | sell_at_target | time_exit",
    )
    exit_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="e.g. {'target_pct': 400.0} or {'days_before_expiry': 3}",
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="open",
        comment="open | closed.",
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Exit
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="resolution | target_hit | time_exit | manual.",
    )

    # P&L
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    market: Mapped[Any] = relationship("Market", lazy="noload")
    live_order: Mapped[LiveOrder | None] = relationship(
        "LiveOrder", back_populates="live_position", lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<LivePosition id={self.id} strategy={self.strategy!r} "
            f"side={self.side!r} entry={self.entry_price} status={self.status!r} "
            f"net_pnl={self.net_pnl}>"
        )
