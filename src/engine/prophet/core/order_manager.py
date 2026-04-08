"""
Order Manager — manages the lifecycle of paper orders and positions.

Paper order flow
----------------
1. Signal approved by RiskManager → :meth:`create_paper_order` → ``PaperOrder``
   with ``status='open'``.
2. :meth:`check_fills` runs every 2 minutes:
   - For each open ``PaperOrder``, query ``observed_trades`` for a real trade
     at or better than the target price.
   - If found: simulate fill with conservative slippage model.
   - On fill: set ``PaperOrder.status='filled'`` and create a ``Position``.
3. :meth:`check_exits` runs every 5 minutes:
   - For each open ``Position``, check its exit condition:
     - ``hold_to_resolution``: close when ``market.resolved_outcome`` is set.
     - ``sell_at_target``: close when current price >= entry * (1 + target_pct/100).
     - ``sell_at_Nx``: close when current price >= entry * N.
4. :meth:`calculate_pnl` computes gross/fees/net PnL when closing.

Paper fill model (conservative)
---------------------------------
- ``queue_multiplier``  : 5.0 — we assume 5× our order size ahead in queue.
- ``slippage_bps``      : 100 — 1% slippage on fill price.
- ``min_volume_usd``    : 25 — minimum USD volume at the target price required.
- Fill only occurs if an actual trade is observed at or below our target price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.polymarket.clob_client import PolymarketClient
from prophet.polymarket.orderbook import OrderBookService

logger = logging.getLogger(__name__)

# Paper fill model constants
_QUEUE_MULTIPLIER = 5.0
_SLIPPAGE_BPS = 100  # 1%
_MIN_VOLUME_USD = 25.0
_FEE_RATE = 0.02  # 2% Polymarket taker fee
_ORDER_EXPIRY_HOURS = 168  # 1 week default expiry


def _simulate_price_impact(raw_book: dict, size_usd: float) -> tuple[float, float]:
    """Walk the order book to calculate the volume-weighted average fill price.

    Parameters
    ----------
    raw_book:
        JSONB snapshot from OrderBookSnapshot — ``{"asks": [{"price": p, "size": s}, ...]}``.
        ``size`` is in shares (contracts), not USD.
    size_usd:
        Order size in USD we want to fill.
    side:
        ``'yes'`` or ``'no'`` — determines which levels to consume.

    Returns
    -------
    (vwap, price_impact_pct)
        vwap: volume-weighted average price paid.
        price_impact_pct: % premium over best_ask (0.0 if no impact data).
    """
    levels = raw_book.get("asks", [])
    if not levels:
        return 0.0, 0.0

    # Sort ascending by price (best ask first)
    levels = sorted(levels, key=lambda x: x["price"])
    best_ask = levels[0]["price"]

    remaining_usd = size_usd
    total_shares = 0.0
    total_cost = 0.0

    for level in levels:
        price = level["price"]
        level_shares = level["size"]
        level_usd = level_shares * price

        if remaining_usd <= 0:
            break

        consumed_usd = min(remaining_usd, level_usd)
        consumed_shares = consumed_usd / price

        total_shares += consumed_shares
        total_cost += consumed_usd
        remaining_usd -= consumed_usd

    if total_shares <= 0 or total_cost <= 0:
        return best_ask, 0.0

    vwap = total_cost / total_shares
    price_impact_pct = ((vwap - best_ask) / best_ask * 100) if best_ask > 0 else 0.0

    return round(vwap, 6), round(price_impact_pct, 4)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderManager:
    """Manages paper order placement, fills, and position exits.

    Each public scheduler job (place_pending_orders, check_fills, check_exits)
    creates its own DB session to avoid concurrent-session errors.

    Parameters
    ----------
    clob_client:
        Started :class:`~prophet.polymarket.clob_client.PolymarketClient`.
    db_session:
        Ignored — kept for backwards-compatibility.
    redis_client:
        Optional async Redis client.
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        db_session: AsyncSession | None = None,
        redis_client: Any | None = None,
        signal_router: Any | None = None,
    ) -> None:
        self._clob = clob_client
        self._redis = redis_client
        self._signal_router = signal_router  # None = always paper
        self._ob_service = OrderBookService(
            clob_client=clob_client,
            db_session=None,
            redis_client=redis_client,
        )

    # ------------------------------------------------------------------
    # Order creation
    # ------------------------------------------------------------------

    async def create_paper_order(self, db: AsyncSession, signal: Any) -> Any:
        """Create a PaperOrder from an approved Signal."""
        from prophet.db.models import PaperOrder, Position

        # Dedup: skip if we already have an open position OR a pending/open paper order
        existing_pos = await db.scalar(
            select(Position.id).where(
                Position.market_id == signal.market_id,
                Position.strategy == signal.strategy,
                Position.side == signal.side,
                Position.status == "open",
            ).limit(1)
        )
        if existing_pos:
            signal.status = "skipped"
            logger.debug(
                "Dedup: skipping signal market_id=%d %s %s — position %d already open",
                signal.market_id, signal.strategy, signal.side, existing_pos,
            )
            return None

        existing_order = await db.scalar(
            select(PaperOrder.id).where(
                PaperOrder.market_id == signal.market_id,
                PaperOrder.strategy == signal.strategy,
                PaperOrder.side == signal.side,
                PaperOrder.status == "open",
            ).limit(1)
        )
        if existing_order:
            signal.status = "skipped"
            logger.debug(
                "Dedup: skipping signal market_id=%d %s %s — paper order %d already open",
                signal.market_id, signal.strategy, signal.side, existing_order,
            )
            return None

        order = PaperOrder(
            signal_id=signal.id,
            market_id=signal.market_id,
            strategy=signal.strategy,
            side=signal.side,
            order_type="limit",
            target_price=signal.target_price,
            size_usd=signal.size_usd,
            status="open",
            placed_at=_utcnow(),
        )
        db.add(order)

        # Mark signal as executed
        signal.status = "executed"

        await db.flush()
        logger.info(
            "PaperOrder created: id=%d market_id=%d %s %s@%.4f $%.2f",
            order.id, order.market_id, order.strategy,
            order.side, order.target_price, order.size_usd,
        )
        return order

    # ------------------------------------------------------------------
    # Pending signal → PaperOrder conversion
    # ------------------------------------------------------------------

    async def place_pending_orders(self) -> int:
        """Convert all pending signals into open paper orders.

        Called every 5 minutes by the scheduler.
        """
        from prophet.db.database import get_session
        from prophet.db.models import Signal

        async with get_session() as db:
            stmt = select(Signal).where(Signal.status == "pending")
            result = await db.execute(stmt)
            pending = list(result.scalars().all())

            if not pending:
                return 0

            placed = 0
            for signal in pending:
                try:
                    if self._signal_router is not None and self._signal_router.route(signal) == "live":
                        from prophet.db.models import Market
                        market = await db.get(Market, signal.market_id)
                        token_id = (
                            market.token_id_yes if signal.side == "YES" else market.token_id_no
                        ) if market else None
                        route = await self._signal_router.dispatch(db, signal, self, token_id)
                        logger.info(
                            "place_pending_orders: signal_id=%d routed to %s", signal.id, route
                        )
                    else:
                        await self.create_paper_order(db, signal)
                    placed += 1
                except Exception as exc:
                    logger.error(
                        "place_pending_orders: failed for signal_id=%d: %s",
                        signal.id, exc,
                    )

            if placed:
                logger.info("place_pending_orders: placed %d order(s)", placed)
            return placed
        return 0  # unreachable

    # ------------------------------------------------------------------
    # Fill checking
    # ------------------------------------------------------------------

    async def check_fills(self) -> int:
        """Check all open paper orders for fills.

        Called every 2 minutes by the scheduler.
        """
        from prophet.db.database import get_session
        from prophet.db.models import PaperOrder

        async with get_session() as db:
            stmt = select(PaperOrder).where(PaperOrder.status == "open")
            result = await db.execute(stmt)
            open_orders = list(result.scalars().all())

            if not open_orders:
                return 0

            filled_count = 0
            for order in open_orders:
                try:
                    filled = await self._try_fill_order(db, order)
                    if filled:
                        filled_count += 1
                except Exception as exc:
                    logger.error(
                        "check_fills: error processing order_id=%d: %s", order.id, exc
                    )

            logger.debug("check_fills: %d/%d orders filled", filled_count, len(open_orders))
            return filled_count
        return 0  # unreachable

    async def _try_fill_order(self, db: AsyncSession, order: Any) -> bool:
        """Attempt to fill one open paper order using orderbook snapshots.

        Returns True if filled.
        """
        from prophet.db.models import OrderBookSnapshot, Position

        # Check expiry
        if order.placed_at:
            age_hours = (
                _utcnow() - order.placed_at.replace(tzinfo=timezone.utc)
                if order.placed_at.tzinfo is None
                else _utcnow() - order.placed_at
            ).total_seconds() / 3600
            if age_hours > _ORDER_EXPIRY_HOURS:
                order.status = "expired"
                order.cancel_reason = f"Expired after {age_hours:.1f}h"
                logger.info(
                    "PaperOrder expired: id=%d age=%.1fh", order.id, age_hours
                )
                return False

        # Get the latest orderbook snapshot for this market/side
        stmt = (
            select(OrderBookSnapshot)
            .where(
                and_(
                    OrderBookSnapshot.market_id == order.market_id,
                    OrderBookSnapshot.side == order.side.lower(),
                )
            )
            .order_by(OrderBookSnapshot.timestamp.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        snapshot = result.scalar_one_or_none()

        if snapshot is None:
            logger.debug(
                "PaperOrder %d: no orderbook snapshot yet for market_id=%d side=%s",
                order.id, order.market_id, order.side,
            )
            return False

        best_ask = snapshot.best_ask
        if best_ask is None or best_ask <= 0:
            return False

        if best_ask > order.target_price:
            logger.debug(
                "PaperOrder %d: best_ask=%.4f > target=%.4f — not filled",
                order.id, best_ask, order.target_price,
            )
            return False

        # Simulate price impact by walking the order book levels
        raw_book = snapshot.raw_book or {}
        fill_price, price_impact_pct = _simulate_price_impact(raw_book, order.size_usd)
        if fill_price <= 0:
            # Fallback to best_ask if raw_book is empty
            fill_price = best_ask
            price_impact_pct = 0.0

        fill_size_usd = order.size_usd
        fill_at = _utcnow()

        order.status = "filled"
        order.fill_price = fill_price
        order.fill_size_usd = fill_size_usd
        order.filled_at = fill_at

        # Dedup: skip position creation if one already exists
        existing_pos = await db.scalar(
            select(Position.id).where(
                Position.market_id == order.market_id,
                Position.strategy == order.strategy,
                Position.side == order.side,
                Position.status == "open",
            ).limit(1)
        )
        if existing_pos:
            logger.debug(
                "PaperOrder %d fill skipped — position %d already open for market_id=%d %s %s",
                order.id, existing_pos, order.market_id, order.strategy, order.side,
            )
            return False

        # Create position
        shares = fill_size_usd / fill_price if fill_price > 0 else 0.0
        position = Position(
            market_id=order.market_id,
            strategy=order.strategy,
            side=order.side,
            entry_price=fill_price,
            size_usd=fill_size_usd,
            shares=shares,
            status="open",
            opened_at=fill_at,
            price_impact_pct=price_impact_pct if price_impact_pct > 0 else None,
        )
        db.add(position)

        logger.info(
            "PaperOrder FILLED: id=%d market_id=%d %s %s@%.4f (ask=%.4f) impact=%.2f%% $%.2f",
            order.id, order.market_id, order.strategy,
            order.side, fill_price, best_ask, price_impact_pct, fill_size_usd,
        )
        return True

    # ------------------------------------------------------------------
    # Exit checking
    # ------------------------------------------------------------------

    async def check_exits(self) -> int:
        """Check all open positions for exit conditions.

        Called every 5 minutes by the scheduler.

        Processes positions in pages of 200 to avoid holding a single enormous
        DB session that blocks other jobs and causes APScheduler misfire warnings.
        Each page is committed independently so progress is never lost.
        """
        from prophet.db.database import get_session
        from prophet.db.models import Position

        _PAGE_SIZE = 200

        # Count open positions first (cheap query)
        async with get_session() as db:
            from sqlalchemy import func
            total_open = await db.scalar(
                select(func.count(Position.id)).where(Position.status == "open")
            ) or 0

        if not total_open:
            return 0

        # Only suppress individual notifs when there are genuinely massive backlogs
        # (>500). During normal operation (<500) send individual notifs per close.
        self._suppress_close_notif = total_open > 500

        closed_count = 0
        offset = 0

        while True:
            async with get_session() as db:
                stmt = (
                    select(Position)
                    .where(Position.status == "open")
                    .order_by(Position.id)
                    .limit(_PAGE_SIZE)
                    .offset(offset)
                )
                result = await db.execute(stmt)
                page = list(result.scalars().all())

                if not page:
                    break

                page_closed = 0
                for position in page:
                    try:
                        closed = await self._check_position_exit(db, position)
                        if closed:
                            closed_count += 1
                            page_closed += 1
                            if page_closed % 10 == 0:
                                await db.commit()
                    except Exception as exc:
                        logger.error(
                            "check_exits: error for position_id=%d: %s", position.id, exc
                        )

                if page_closed > 0:
                    await db.commit()

                # If this page had fewer rows than PAGE_SIZE, we're done
                if len(page) < _PAGE_SIZE:
                    break

                # Advance offset by how many were NOT closed (closed rows
                # disappear from the next query so offset stays correct)
                offset += len(page) - page_closed

        self._suppress_close_notif = False

        if closed_count > 0:
            logger.info("check_exits: %d/%d positions closed", closed_count, total_open)
            if total_open > 500:
                try:
                    from prophet.core.telegram_bot import notifier
                    if notifier.enabled:
                        await notifier._send(
                            f"📊 <b>Batch close</b>: {closed_count}/{total_open} posiciones cerradas"
                        )
                except Exception:
                    pass
        else:
            logger.debug("check_exits: 0/%d positions closed", total_open)

        return closed_count

    async def _check_position_exit(self, db: AsyncSession, position: Any) -> bool:
        """Evaluate exit conditions for one open position.

        Returns True if position was closed.
        """
        from prophet.db.models import Market

        # Look up the signal to get exit_strategy
        exit_strategy, exit_params = await self._get_exit_info(db, position)

        # Fetch current market state
        market_stmt = select(Market).where(Market.id == position.market_id)
        market_result = await db.execute(market_stmt)
        market = market_result.scalar_one_or_none()
        if market is None:
            return False

        # Attach market info for Telegram notifications
        position._market_question = market.question
        # polymarket.com/event/{slug} gives 404 for group markets — use condition_id instead
        cid = getattr(market, "condition_id", None)
        position._market_url = f"https://polymarket.com/market/{cid}" if cid else ""

        # ── Fast path: market already resolved → close immediately ─────
        if market.resolved_outcome:
            exit_price = self._resolution_exit_price(
                position.side, market.resolved_outcome
            )
            return await self._close_position(
                position,
                exit_price=exit_price,
                exit_reason="resolution",
            )

        # ── hold_to_resolution ──────────────────────────────────────────
        if exit_strategy == "hold_to_resolution":
            if market.resolved_outcome:
                exit_price = self._resolution_exit_price(
                    position.side, market.resolved_outcome
                )
                return await self._close_position(
                    position,
                    exit_price=exit_price,
                    exit_reason="resolution",
                )
            return False

        # ── sell_at_target ──────────────────────────────────────────────
        if exit_strategy == "sell_at_target":
            target_pct = float(exit_params.get("target_pct", 100.0))
            target_price = position.entry_price * (1.0 + target_pct / 100.0)
            target_price = min(target_price, 1.0)

            current_price = await self._get_current_price(position)
            if current_price is not None and current_price >= target_price:
                return await self._close_position(
                    position,
                    exit_price=current_price,
                    exit_reason="target_hit",
                )

            # Also close at resolution if market resolves
            if market.resolved_outcome:
                exit_price = self._resolution_exit_price(
                    position.side, market.resolved_outcome
                )
                return await self._close_position(
                    position,
                    exit_price=exit_price,
                    exit_reason="resolution",
                )

            # Timeout check
            timeout_hours = float(exit_params.get("timeout_hours", 0))
            if timeout_hours > 0 and position.opened_at:
                opened = position.opened_at
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                age_hours = (_utcnow() - opened).total_seconds() / 3600
                if age_hours >= timeout_hours:
                    current_price = current_price or position.entry_price
                    return await self._close_position(
                        position,
                        exit_price=current_price,
                        exit_reason="expired",
                    )

            return False

        # ── sell_at_Nx ──────────────────────────────────────────────────
        if exit_strategy.startswith("sell_at_"):
            multiplier = float(exit_params.get("multiplier", 2.0))
            target_price = min(position.entry_price * multiplier, 1.0)
            current_price = await self._get_current_price(position)
            if current_price is not None and current_price >= target_price:
                return await self._close_position(
                    position,
                    exit_price=current_price,
                    exit_reason="target_hit",
                )
            if market.resolved_outcome:
                exit_price = self._resolution_exit_price(
                    position.side, market.resolved_outcome
                )
                return await self._close_position(
                    position,
                    exit_price=exit_price,
                    exit_reason="resolution",
                )
            return False

        # ── time_exit ──────────────────────────────────────────────────
        if exit_strategy == "time_exit":
            target_pct = float(exit_params.get("target_pct", 100.0))
            target_price = min(position.entry_price * (1.0 + target_pct / 100.0), 1.0)

            current_price = await self._get_current_price(position)

            # 1. Target hit — best outcome
            if current_price is not None and current_price >= target_price:
                return await self._close_position(
                    position, exit_price=current_price, exit_reason="target_hit",
                )

            # 2. Time-based exit: N days before resolution_date
            days_before = float(exit_params.get("days_before_expiry", 3.0))
            if market.resolution_date:
                from datetime import date as _date
                today = _utcnow().date()
                days_remaining = (market.resolution_date - today).days
                if days_remaining <= days_before:
                    sell_price = current_price if current_price is not None else position.entry_price * 0.3
                    sell_price = max(sell_price, 0.001)
                    return await self._close_position(
                        position, exit_price=sell_price, exit_reason="time_exit",
                    )

            # 3. Fallback: resolution (shouldn't normally hit this with time_exit)
            if market.resolved_outcome:
                exit_price = self._resolution_exit_price(
                    position.side, market.resolved_outcome
                )
                return await self._close_position(
                    position, exit_price=exit_price, exit_reason="resolution",
                )

            return False

        logger.warning(
            "Unknown exit_strategy %r for position_id=%d", exit_strategy, position.id
        )
        return False

    async def _close_position(
        self, position: Any, exit_price: float, exit_reason: str
    ) -> bool:
        """Mark a position as closed and compute PnL."""
        gross_pnl, fees, net_pnl = self.calculate_pnl(position, exit_price, exit_reason)
        position.status = "closed"
        position.closed_at = _utcnow()
        position.exit_price = exit_price
        position.exit_reason = exit_reason
        position.gross_pnl = gross_pnl
        position.fees = fees
        position.net_pnl = net_pnl

        logger.info(
            "Position CLOSED: id=%d %s %s entry=%.4f exit=%.4f "
            "net_pnl=$%.2f reason=%s",
            position.id, position.strategy, position.side,
            position.entry_price, exit_price, net_pnl, exit_reason,
        )

        # Telegram notification (fire-and-forget, skip in batch mode)
        try:
            from prophet.core.telegram_bot import notifier
            if notifier.enabled and not getattr(self, '_suppress_close_notif', False):
                market_question = getattr(position, "_market_question", None) or f"Market #{position.market_id}"
                market_url = getattr(position, "_market_url", None) or ""
                import asyncio
                asyncio.ensure_future(notifier.notify_trade_closed(
                    strategy=position.strategy,
                    market_question=market_question,
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    net_pnl=net_pnl,
                    exit_reason=exit_reason,
                    market_url=market_url,
                ))
        except Exception:
            pass  # never let notifications break trading

        return True

    # ------------------------------------------------------------------
    # PnL calculation
    # ------------------------------------------------------------------

    def calculate_pnl(
        self, position: Any, exit_price: float, exit_reason: str = ""
    ) -> tuple[float, float, float]:
        """Calculate gross PnL, fees, and net PnL for a position.

        Polymarket fee structure (March 2026):
        - Makers (limit orders): 0% fee
        - Takers: fee = shares * feeRate * p * (1-p), crypto feeRate=0.072
        - Resolution payouts: 0% fee

        We assume entry is always maker (limit order / stink bid).
        Exit via resolution = 0 fee. Exit via sell = taker fee (conservative).
        """
        shares = position.shares or (
            position.size_usd / position.entry_price if position.entry_price else 0.0
        )
        gross_pnl = (exit_price - position.entry_price) * shares
        # Entry: maker (limit order) → 0 fee
        # Exit: resolution → 0 fee, sell → taker fee
        if exit_reason.startswith("resolution"):
            fees = 0.0
        else:
            fees = shares * 0.072 * exit_price * (1 - exit_price)
        net_pnl = gross_pnl - fees
        return round(gross_pnl, 4), round(fees, 4), round(net_pnl, 4)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_exit_info(
        self, db: AsyncSession, position: Any
    ) -> tuple[str, dict[str, Any]]:
        """Return (exit_strategy, exit_params) for a position."""
        try:
            from prophet.db.models import PaperOrder, Signal

            stmt = (
                select(PaperOrder)
                .where(
                    and_(
                        PaperOrder.market_id == position.market_id,
                        PaperOrder.strategy == position.strategy,
                        PaperOrder.side == position.side,
                        PaperOrder.status == "filled",
                    )
                )
                .order_by(PaperOrder.filled_at.desc())
                .limit(1)
            )
            result = await db.execute(stmt)
            order = result.scalar_one_or_none()

            if order and order.signal_id:
                signal_stmt = select(Signal).where(Signal.id == order.signal_id)
                signal_result = await db.execute(signal_stmt)
                signal = signal_result.scalar_one_or_none()
                if signal and signal.params:
                    exit_strategy = signal.params.get("exit_strategy", "hold_to_resolution")
                    exit_params = signal.params.get("exit_params", {})
                    return exit_strategy, exit_params
        except Exception as exc:
            logger.debug("_get_exit_info error: %s", exc)

        return "hold_to_resolution", {}

    async def _get_current_price(self, position: Any) -> float | None:
        """Get the current best_bid for a position's side.

        We use best_bid (not mid_price) because that's the price you'd
        actually get if you sold.  Mid_price is misleading on wide spreads
        — e.g. bid=0.002, ask=0.899 gives mid=0.45 which is unrealistic.
        """
        try:
            from prophet.db.database import get_session
            from prophet.db.models import Market

            async with get_session() as db:
                market_stmt = select(Market).where(Market.id == position.market_id)
                market_result = await db.execute(market_stmt)
                market = market_result.scalar_one_or_none()
                if market is None:
                    return None

            token_id = (
                market.token_id_yes if position.side == "YES" else market.token_id_no
            )

            # Try cached order book first
            side_key = "yes" if position.side == "YES" else "no"
            cached = await self._ob_service.get_cached_book(position.market_id, side_key)
            if cached and cached.best_bid is not None:
                return cached.best_bid

            # Live fetch
            book = await self._ob_service.fetch_and_compute(token_id)
            return book.best_bid
        except Exception as exc:
            logger.debug("_get_current_price error: %s", exc)
            return None

    @staticmethod
    def _resolution_exit_price(side: str, resolved_outcome: str) -> float:
        """Return the exit price based on resolution outcome."""
        side = side.upper()
        outcome = str(resolved_outcome).upper()
        if (side == "YES" and outcome == "YES") or (side == "NO" and outcome == "NO"):
            return 1.0
        return 0.0
