"""
LiveTrader — places real CLOB orders and tracks fills.

Flow
----
1. Signal arrives (from signal_router)
2. LiveRiskManager.check() — hard limits gate
3. clob.place_order() — real order on-chain
4. LiveOrder created with clob_order_id
5. check_live_fills() (every 2 min) — polls CLOB for fill status
6. On fill: LivePosition created with actual fill price + slippage
7. check_live_exits() (every 5 min) — same logic as paper exits

Separation guarantee
--------------------
This module NEVER touches: signals, paper_orders, positions tables.
Paper module NEVER touches: live_orders, live_positions tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.polymarket.clob_client import PolymarketClient
from prophet.live.live_risk import LiveRiskManager

logger = logging.getLogger(__name__)

_FEE_RATE = 0.02  # 2% Polymarket taker fee
_ORDER_EXPIRY_HOURS = 168  # 1 week


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_HEARTBEAT_INTERVAL_S = 5  # send heartbeat every 5s when live orders open


class LiveTrader:
    """Executes real orders and manages live position lifecycle.

    Parameters
    ----------
    clob_client:
        Authenticated PolymarketClient (requires PAPER_TRADING=false).
    risk_manager:
        LiveRiskManager with hard limits.
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        risk_manager: LiveRiskManager,
    ) -> None:
        self._clob = clob_client
        self._risk = risk_manager
        self._heartbeat_id: str | None = None  # rolling heartbeat token

    # ------------------------------------------------------------------
    # Entry — place order
    # ------------------------------------------------------------------

    async def place_live_order(
        self,
        db: AsyncSession,
        signal: Any,
        token_id: str,
    ) -> Any | None:
        """Place a live order from a routed signal.

        Returns the LiveOrder on success, None if blocked by risk or CLOB error.
        """
        from prophet.live.live_models import LiveOrder

        allowed, reason = await self._risk.check(db, signal.strategy, signal.size_usd)
        if not allowed:
            logger.info("[LIVE] BLOCKED strategy=%s reason=%s", signal.strategy, reason)
            return None

        shares = signal.size_usd / signal.target_price

        # Create order record first (status=pending) so we have a DB row even if CLOB fails
        live_order = LiveOrder(
            market_id=signal.market_id,
            strategy=signal.strategy,
            side=signal.side,
            token_id=token_id,
            target_price=signal.target_price,
            size_usd=signal.size_usd,
            shares_requested=shares,
            status="pending",
            params=getattr(signal, "params", {}),
        )
        db.add(live_order)
        await db.flush()

        # Place on CLOB
        try:
            clob_order_id = await self._clob.place_order(
                token_id=token_id,
                side="BUY",
                price=signal.target_price,
                size=shares,
            )
            live_order.clob_order_id = clob_order_id
            live_order.status = "open"
            await db.commit()

            logger.info(
                "[LIVE] Order placed: id=%d clob_id=%s strategy=%s %s@%.4f $%.2f",
                live_order.id, clob_order_id, signal.strategy,
                signal.side, signal.target_price, signal.size_usd,
            )
            await self._notify_entry(live_order, signal)
            return live_order

        except Exception as exc:
            live_order.status = "failed"
            live_order.error_msg = str(exc)[:500]
            await db.commit()
            logger.error("[LIVE] Order placement failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Fill checking — poll CLOB for order status
    # ------------------------------------------------------------------

    async def check_live_fills(self) -> int:
        """Poll CLOB for fill status of all open live orders.

        Called every 2 minutes by the scheduler.
        Returns count of newly filled orders.
        """
        from prophet.db.database import get_session
        from prophet.live.live_models import LiveOrder

        async with get_session() as db:
            stmt = select(LiveOrder).where(LiveOrder.status == "open")
            result = await db.execute(stmt)
            open_orders = list(result.scalars().all())

            if not open_orders:
                return 0

            filled = 0
            for order in open_orders:
                try:
                    if await self._check_order_fill(db, order):
                        filled += 1
                except Exception as exc:
                    logger.error("[LIVE] check_fills error for order_id=%d: %s", order.id, exc)

            logger.debug("[LIVE] check_live_fills: %d/%d filled", filled, len(open_orders))
            return filled

    async def _check_order_fill(self, db: AsyncSession, order: Any) -> bool:
        """Check if a single live order has been filled. Returns True if filled."""
        from prophet.live.live_models import LivePosition

        if not order.clob_order_id:
            return False

        # Expire old unfilled orders
        age_hours = (_utcnow() - order.placed_at.replace(tzinfo=timezone.utc)
                     if order.placed_at.tzinfo is None
                     else (_utcnow() - order.placed_at)).total_seconds() / 3600
        if age_hours > _ORDER_EXPIRY_HOURS:
            order.status = "cancelled"
            order.error_msg = "Expired after 1 week without fill"
            await db.commit()
            return False

        # Query CLOB for order status
        try:
            clob_data = await self._clob.get_order(order.clob_order_id)
        except Exception as exc:
            logger.warning("[LIVE] Could not fetch order %s: %s", order.clob_order_id, exc)
            return False

        status = clob_data.get("status", "").lower()
        size_matched = float(clob_data.get("size_matched", 0) or 0)
        avg_price = float(clob_data.get("price", order.target_price) or order.target_price)

        if status in ("matched", "filled") or size_matched >= order.shares_requested * 0.95:
            # Filled
            fill_usd = size_matched * avg_price
            slippage_pct = (avg_price - order.target_price) / order.target_price * 100

            order.status = "filled"
            order.filled_at = _utcnow()
            order.fill_price_actual = avg_price
            order.fill_size_usd_actual = fill_usd
            order.slippage_pct = slippage_pct

            # Create live position
            params = order.params or {}
            position = LivePosition(
                market_id=order.market_id,
                live_order_id=order.id,
                strategy=order.strategy,
                side=order.side,
                token_id=order.token_id,
                entry_price=avg_price,
                entry_price_target=order.target_price,
                size_usd=fill_usd,
                shares=size_matched,
                slippage_pct=slippage_pct,
                exit_strategy=params.get("exit_strategy", "hold_to_resolution"),
                exit_params={k: v for k, v in params.items()
                             if k in ("target_pct", "days_before_expiry")},
                status="open",
                opened_at=_utcnow(),
            )
            db.add(position)
            await db.commit()

            logger.info(
                "[LIVE] Filled: order_id=%d strategy=%s %s fill_price=%.4f "
                "target=%.4f slippage=%.1f%%",
                order.id, order.strategy, order.side,
                avg_price, order.target_price, slippage_pct,
            )
            await self._notify_fill(order, position)
            return True

        elif status in ("cancelled", "canceled"):
            order.status = "cancelled"
            await db.commit()

        return False

    # ------------------------------------------------------------------
    # Exit checking — same logic as paper, but for live positions
    # ------------------------------------------------------------------

    async def check_live_exits(self) -> int:
        """Check all open live positions for exit conditions.

        Called every 5 minutes by the scheduler.
        Returns count of positions closed.
        """
        from prophet.db.database import get_session
        from prophet.live.live_models import LivePosition

        async with get_session() as db:
            stmt = select(LivePosition).where(LivePosition.status == "open")
            result = await db.execute(stmt)
            open_positions = list(result.scalars().all())

            if not open_positions:
                return 0

            closed = 0
            for pos in open_positions:
                try:
                    if await self._check_exit(db, pos):
                        closed += 1
                        # Commit per-position to avoid batch rollback
                        await db.commit()
                except Exception as exc:
                    logger.error("[LIVE] check_exits error for pos_id=%d: %s", pos.id, exc)
                    await db.rollback()

            logger.info("[LIVE] check_live_exits: %d/%d positions closed", closed, len(open_positions))
            return closed

    async def _check_exit(self, db: AsyncSession, pos: Any) -> bool:
        """Check if a live position should be exited. Returns True if closed."""
        from prophet.db.models import Market

        # Load market
        market = await db.get(Market, pos.market_id)
        if not market:
            return False

        # Fast path: market already resolved in DB
        if market.resolved_outcome is not None:
            resolution_win = (
                (pos.side == "YES" and market.resolved_outcome == "YES") or
                (pos.side == "NO" and market.resolved_outcome == "NO")
            )
            exit_price = 1.0 if resolution_win else 0.0
            exit_reason = "resolution"
            return await self._close_position(db, pos, exit_price, exit_reason)

        exit_type = pos.exit_strategy or "hold_to_resolution"
        exit_params = pos.exit_params or {}

        if exit_type == "sell_at_target":
            # target_pct=400 means sell at 5× (entry × (1 + 4.0))
            target_pct = float(exit_params.get("target_pct", 300.0))
            target_price = pos.entry_price * (1 + target_pct / 100.0)
            # Get current price from CLOB order book
            current_price = await self._get_current_price(pos.token_id, pos.side)
            if current_price and current_price >= target_price:
                return await self._close_position(db, pos, current_price, "target_hit")

        elif exit_type == "time_exit":
            days_before = exit_cfg.get("days_before_expiry", 3)
            if market.resolution_date:
                days_to_expiry = (market.resolution_date - _utcnow().date()).days
                if days_to_expiry <= days_before:
                    current_price = await self._get_current_price(pos.token_id, pos.side)
                    if current_price:
                        return await self._close_position(db, pos, current_price, "time_exit")

        return False

    async def _get_current_price(self, token_id: str, side: str) -> float | None:
        """Get current best bid/ask from CLOB for a token."""
        try:
            book = await self._clob.get_order_book(token_id)
            if side == "YES":
                # To sell YES shares: look at bid side (what buyers will pay)
                bids = getattr(book, "bids", [])
                if bids:
                    return float(bids[0].price)
            else:
                bids = getattr(book, "bids", [])
                if bids:
                    return float(bids[0].price)
        except Exception as exc:
            logger.warning("[LIVE] Could not get price for token %s: %s", token_id[:16], exc)
        return None

    async def _close_position(
        self,
        db: AsyncSession,
        pos: Any,
        exit_price: float,
        exit_reason: str,
    ) -> bool:
        """Mark a live position as closed and compute P&L."""
        gross_pnl = (exit_price - pos.entry_price) * pos.shares
        # Fees: entry fee already paid; exit fee only if we sell (not resolution)
        exit_fee = pos.size_usd * _FEE_RATE if exit_reason != "resolution" else 0.0
        entry_fee = pos.size_usd * _FEE_RATE
        fees = entry_fee + exit_fee
        net_pnl = gross_pnl - fees

        pos.status = "closed"
        pos.closed_at = _utcnow()
        pos.exit_price = exit_price
        pos.exit_reason = exit_reason
        pos.gross_pnl = gross_pnl
        pos.fees = fees
        pos.net_pnl = net_pnl

        logger.info(
            "[LIVE] Closed: pos_id=%d strategy=%s %s exit_reason=%s "
            "entry=%.4f exit=%.4f net_pnl=$%.2f",
            pos.id, pos.strategy, pos.side, exit_reason,
            pos.entry_price, exit_price, net_pnl,
        )
        await self._notify_exit(pos)
        return True

    # ------------------------------------------------------------------
    # Heartbeat — keeps live orders alive on the CLOB
    # ------------------------------------------------------------------

    async def send_heartbeat(self) -> None:
        """Send CLOB heartbeat to prevent auto-cancellation of open orders.

        The CLOB cancels ALL open orders if no heartbeat is received within 10s.
        This should be called every 5s by the scheduler when live orders are open.
        Skips silently if no open live orders exist (avoids unnecessary auth calls).
        """
        from prophet.db.database import get_session
        from prophet.live.live_models import LiveOrder

        # Only send heartbeat if there are open live orders
        async with get_session() as db:
            count = await db.scalar(
                __import__("sqlalchemy").select(
                    __import__("sqlalchemy").func.count(LiveOrder.id)
                ).where(LiveOrder.status == "open")
            )

        if not count:
            return  # no open orders → no heartbeat needed

        try:
            self._heartbeat_id = await self._clob.post_heartbeat(self._heartbeat_id)
            logger.debug("[LIVE] Heartbeat sent (open_orders=%d)", count)
        except Exception as exc:
            logger.error("[LIVE] Heartbeat FAILED — open orders may be at risk: %s", exc)
            # Alert Telegram
            try:
                from prophet.core.telegram_bot import notifier
                if notifier.enabled:
                    await notifier._send(
                        f"🔴 <b>[LIVE] Heartbeat FAILED</b>\n"
                        f"Open orders at risk of auto-cancellation!\n{exc}"
                    )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Telegram notifications tagged [LIVE]
    # ------------------------------------------------------------------

    async def _notify_entry(self, order: Any, signal: Any) -> None:
        try:
            from prophet.core.telegram_bot import notifier
            if not notifier.enabled:
                return
            await notifier._send(
                f"🟡 <b>[LIVE] Order placed</b>\n"
                f"Strategy: <code>{order.strategy}</code>\n"
                f"Side: {order.side} | Target: ${order.target_price:.4f} | "
                f"Size: ${order.size_usd:.2f}\n"
                f"CLOB ID: <code>{order.clob_order_id}</code>"
            )
        except Exception:
            pass

    async def _notify_fill(self, order: Any, pos: Any) -> None:
        try:
            from prophet.core.telegram_bot import notifier
            if not notifier.enabled:
                return
            slippage_str = f"{pos.slippage_pct:+.1f}%" if pos.slippage_pct is not None else "?"
            await notifier._send(
                f"🟢 <b>[LIVE] Position opened</b>\n"
                f"Strategy: <code>{pos.strategy}</code>\n"
                f"Side: {pos.side} | Fill: ${pos.entry_price:.4f} "
                f"(target ${pos.entry_price_target:.4f}, slippage {slippage_str})\n"
                f"Shares: {pos.shares:.2f} | Capital: ${pos.size_usd:.2f}"
            )
        except Exception:
            pass

    async def _notify_exit(self, pos: Any) -> None:
        try:
            from prophet.core.telegram_bot import notifier
            if not notifier.enabled:
                return
            emoji = "🟢" if (pos.net_pnl or 0) >= 0 else "🔴"
            await notifier._send(
                f"{emoji} <b>[LIVE] Position closed</b>\n"
                f"Strategy: <code>{pos.strategy}</code>\n"
                f"Side: {pos.side} | Exit: {pos.exit_reason}\n"
                f"Entry: ${pos.entry_price:.4f} → Exit: ${pos.exit_price:.4f}\n"
                f"Net PnL: <b>${pos.net_pnl:.2f}</b>"
            )
        except Exception:
            pass
