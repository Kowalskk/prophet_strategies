"""
Data Collector — orchestrates all periodic data collection.

Each public method creates its own DB session so concurrent scheduler jobs
cannot conflict over a shared session (``Session is already flushing``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from prophet.polymarket.clob_client import PolymarketClient

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DataCollector:
    """Orchestrates all periodic data collection for the Prophet engine.

    Parameters
    ----------
    clob_client:
        Started :class:`~prophet.polymarket.clob_client.PolymarketClient`.
    redis_client:
        Async Redis client (may be None).
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        redis_client: Any | None = None,
        # legacy parameter kept for backwards compat — ignored
        db_session: Any | None = None,
    ) -> None:
        self._clob = clob_client
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Public collection methods (called by Scheduler)
    # Each creates its own DB session to avoid cross-job session conflicts.
    # ------------------------------------------------------------------

    async def collect_prices(self) -> dict[str, Any]:
        """Fetch and persist BTC/ETH/SOL spot prices."""
        from prophet.db.database import get_session_factory
        from prophet.polymarket.price_feeds import PriceFeedService

        sf = get_session_factory()
        async with sf() as session:
            try:
                price_service = PriceFeedService(
                    db_session=session, redis_client=self._redis
                )
                await price_service.start()
                prices = await price_service.fetch_all()
                await session.commit()
                result = {sym: p.price_usd for sym, p in prices.items()}
                logger.debug("collect_prices: %s", result)
                return result
            except Exception as exc:
                await session.rollback()
                logger.error("collect_prices failed: %s", exc)
                return {}

    async def collect_orderbooks(self) -> int:
        """Snapshot order books for all active markets using batch API.

        Uses ``POST /books`` to fetch order books in bulk instead of one-by-one,
        reducing ~3600 HTTP calls to ~72 batch calls (50 tokens per batch).
        """
        from prophet.db.database import get_session_factory
        from prophet.polymarket.orderbook import compute_metrics
        from prophet.db.models import Market, OrderBookSnapshot
        from sqlalchemy import select

        sf = get_session_factory()
        async with sf() as session:
            try:
                crypto_result = await session.execute(
                    select(Market).where(
                        Market.status == "active",
                        Market.category == "crypto",
                    )
                )
                non_crypto_result = await session.execute(
                    select(Market).where(
                        Market.status == "active",
                        Market.category != "crypto",
                        Market.category.isnot(None),
                        Market.volume_usd >= 1000,
                    ).order_by(Market.volume_usd.desc()).limit(1500)
                )
                markets = list(crypto_result.scalars().all()) + list(non_crypto_result.scalars().all())
            except Exception as exc:
                logger.error("collect_orderbooks: failed to load markets: %s", exc)
                return 0

            if not markets:
                return 0

            # Build token_id → (market_id, side) mapping for all markets
            token_map: dict[str, tuple[int, str]] = {}
            all_token_ids: list[str] = []
            for market in markets:
                if market.token_id_yes:
                    token_map[market.token_id_yes] = (market.id, "yes")
                    all_token_ids.append(market.token_id_yes)
                if market.token_id_no:
                    token_map[market.token_id_no] = (market.id, "no")
                    all_token_ids.append(market.token_id_no)

            logger.info(
                "collect_orderbooks: fetching %d tokens (%d markets) via batch API",
                len(all_token_ids), len(markets),
            )

            # Batch fetch all order books
            books = await self._clob.get_order_books_batch(all_token_ids)

            # Persist snapshots
            processed_markets: set[int] = set()
            now = _utcnow()
            for tid, book in books.items():
                mapping = token_map.get(tid)
                if not mapping:
                    continue
                market_id, side = mapping
                book = compute_metrics(book)

                raw_book = {
                    "bids": [{"price": l.price, "size": l.size} for l in book.bids],
                    "asks": [{"price": l.price, "size": l.size} for l in book.asks],
                }
                snapshot = OrderBookSnapshot(
                    market_id=market_id,
                    token_id=tid,
                    side=side,
                    timestamp=now,
                    best_bid=book.best_bid,
                    best_ask=book.best_ask,
                    bid_depth_10pct=book.bid_depth_10pct,
                    ask_depth_10pct=book.ask_depth_10pct,
                    spread_pct=book.spread_pct,
                    raw_book=raw_book,
                )
                session.add(snapshot)
                processed_markets.add(market_id)

            try:
                await session.commit()
            except Exception as exc:
                logger.error("collect_orderbooks: commit failed: %s", exc)
                await session.rollback()

            processed = len(processed_markets)
            skipped = len(markets) - processed
            logger.info(
                "collect_orderbooks: snapshotted %d/%d markets (%d skipped — no OB on CLOB)",
                processed, len(markets), skipped,
            )
            return processed

    # Rolling index so each 2-min cycle processes a different slice of markets
    _trades_offset: int = 0
    _TRADES_PAGE: int = 50  # markets per cycle — keeps each run well under 2 min

    async def collect_trades(self) -> int:
        """Fetch and persist recent trades for markets with open positions.

        Processes _TRADES_PAGE markets per cycle in round-robin order so the
        job always finishes within the 2-min scheduler window, even with 600+
        markets. Full rotation completes every ~24 min (600/50 × 2 min).
        Uses the Data API (no auth required).
        """
        from prophet.db.database import get_session_factory
        from sqlalchemy import select

        sf = get_session_factory()
        async with sf() as session:
            try:
                from prophet.db.models import Market, Position
                market_ids_with_positions = (
                    select(Position.market_id)
                    .where(Position.status == "open")
                    .distinct()
                    .scalar_subquery()
                )
                result = await session.execute(
                    select(Market)
                    .where(
                        Market.status == "active",
                        Market.id.in_(market_ids_with_positions),
                    )
                    .order_by(Market.id)
                )
                all_markets = list(result.scalars().all())
            except Exception as exc:
                logger.error("collect_trades: failed to load markets: %s", exc)
                return 0

            if not all_markets:
                return 0

            # Slice this cycle's page using rolling offset
            offset = self.__class__._trades_offset % len(all_markets)
            page = (all_markets + all_markets)[offset: offset + self._TRADES_PAGE]
            self.__class__._trades_offset = (offset + self._TRADES_PAGE) % len(all_markets)

            total_trades = 0
            for market in page:
                try:
                    count = await self._collect_trades_for_market(session, market)
                    total_trades += count
                except Exception as exc:
                    logger.error(
                        "collect_trades: failed for market_id=%d: %s", market.id, exc
                    )

            if total_trades:
                try:
                    await session.commit()
                except Exception as exc:
                    logger.error("collect_trades: commit failed: %s", exc)
                    await session.rollback()

            logger.debug(
                "collect_trades: %d trades across %d/%d markets (offset=%d)",
                total_trades, len(page), len(all_markets), offset,
            )
            return total_trades

    async def collect_market_status(self) -> int:
        """Refresh market status — expire markets past their resolution_date."""
        from prophet.db.database import get_session_factory
        from sqlalchemy import select

        sf = get_session_factory()
        async with sf() as session:
            try:
                from prophet.db.models import Market
                result = await session.execute(
                    select(Market).where(Market.status == "active")
                )
                markets = list(result.scalars().all())
            except Exception as exc:
                logger.error("collect_market_status: failed to load markets: %s", exc)
                return 0

            updated = 0
            for market in markets:
                try:
                    if (
                        market.resolution_date
                        and market.resolution_date < _utcnow().date()
                        and market.status == "active"
                    ):
                        market.status = "expired"
                        logger.info(
                            "Market expired (past resolution_date): market_id=%d",
                            market.id,
                        )
                        updated += 1
                except Exception as exc:
                    logger.error(
                        "collect_market_status: failed for market_id=%d: %s",
                        market.id, exc,
                    )

            if updated:
                try:
                    await session.commit()
                except Exception as exc:
                    logger.error("collect_market_status: commit failed: %s", exc)
                    await session.rollback()

            logger.debug("collect_market_status: updated %d market(s)", updated)
            return updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_trades_for_market(self, session: Any, market: Any) -> int:
        """Fetch recent trades for YES and NO tokens and persist new ones."""
        from prophet.db.models import ObservedTrade

        new_count = 0
        for token_id, side_label in [
            (market.token_id_yes, "YES"),
            (market.token_id_no, "NO"),
        ]:
            try:
                trades = await self._clob.get_trades(token_id, limit=50)
                for trade in trades:
                    trade_row = ObservedTrade(
                        market_id=market.id,
                        token_id=token_id,
                        side=side_label,
                        price=trade.price,
                        size_usd=trade.size_usd,
                        timestamp=trade.timestamp,
                        maker=trade.maker_address,
                        taker=trade.taker_address,
                    )
                    session.add(trade_row)
                    new_count += 1

                if trades:
                    await session.flush()

            except Exception as exc:
                logger.warning(
                    "_collect_trades_for_market: token=%s side=%s: %s",
                    str(token_id)[:12], side_label, exc,
                )

        return new_count
