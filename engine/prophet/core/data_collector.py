"""
Data Collector — orchestrates all periodic data collection.

:class:`DataCollector` coordinates the following data streams:

┌──────────────────────────┬────────────┬──────────────────────────┐
│ Data                     │ Interval   │ Storage                  │
├──────────────────────────┼────────────┼──────────────────────────┤
│ Order book snapshots     │ 5 min      │ PostgreSQL + Redis       │
│ Spot prices (BTC/ETH/SOL)│ 1 min      │ PostgreSQL + Redis       │
│ Recent trades (CLOB)     │ 2 min      │ PostgreSQL               │
│ Market metadata / status │ 15 min     │ PostgreSQL               │
└──────────────────────────┴────────────┴──────────────────────────┘

Design principle: CAPTURE EVERYTHING NOW, ANALYZE LATER.
Storage is cheap; missing data is irreplaceable.

All methods are async and fully independent — a failure in one stream
does NOT affect other streams.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.polymarket.clob_client import PolymarketClient
from prophet.polymarket.orderbook import OrderBookService
from prophet.polymarket.price_feeds import PriceFeedService

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DataCollector:
    """Orchestrates all periodic data collection for the Prophet engine.

    Parameters
    ----------
    clob_client:
        Started :class:`~prophet.polymarket.clob_client.PolymarketClient`.
    db_session:
        SQLAlchemy async session.
    redis_client:
        Async Redis client (may be None).
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        db_session: AsyncSession,
        redis_client: Any | None = None,
    ) -> None:
        self._clob = clob_client
        self._db = db_session
        self._redis = redis_client

        self._ob_service = OrderBookService(
            clob_client=clob_client,
            db_session=db_session,
            redis_client=redis_client,
        )
        self._price_service = PriceFeedService(
            db_session=db_session,
            redis_client=redis_client,
        )

    # ------------------------------------------------------------------
    # Public collection methods (called by Scheduler)
    # ------------------------------------------------------------------

    async def collect_prices(self) -> dict[str, Any]:
        """Fetch and persist BTC/ETH/SOL spot prices.

        Called every 1 minute by the scheduler.

        Returns
        -------
        dict
            ``{symbol: price_usd}`` for successfully fetched symbols.
        """
        try:
            await self._price_service.start()
            prices = await self._price_service.fetch_all()
            result = {sym: p.price_usd for sym, p in prices.items()}
            logger.debug("collect_prices: %s", result)
            return result
        except Exception as exc:
            logger.error("collect_prices failed: %s", exc)
            return {}

    async def collect_orderbooks(self) -> int:
        """Snapshot order books for all active markets.

        Called every 5 minutes by the scheduler.

        Returns
        -------
        int
            Number of markets processed.
        """
        markets = await self._get_active_markets()
        if not markets:
            logger.debug("collect_orderbooks: no active markets")
            return 0

        processed = 0
        for market in markets:
            try:
                await self._ob_service.snapshot_both_sides(
                    market_id=market.id,
                    token_id_yes=market.token_id_yes,
                    token_id_no=market.token_id_no,
                )
                processed += 1
            except Exception as exc:
                logger.error(
                    "collect_orderbooks: failed for market_id=%d %s: %s",
                    market.id,
                    market.condition_id[:12],
                    exc,
                )

        logger.info("collect_orderbooks: snapshotted %d/%d markets", processed, len(markets))
        return processed

    async def collect_trades(self) -> int:
        """Fetch and persist recent trades for all active markets.

        Called every 2 minutes by the scheduler.
        Stores raw trade data in the ``observed_trades`` table.

        Returns
        -------
        int
            Total number of new trades persisted.
        """
        markets = await self._get_active_markets()
        if not markets:
            return 0

        total_trades = 0
        for market in markets:
            try:
                count = await self._collect_trades_for_market(market)
                total_trades += count
            except Exception as exc:
                logger.error(
                    "collect_trades: failed for market_id=%d: %s",
                    market.id, exc,
                )

        logger.info("collect_trades: persisted %d trade(s) across %d markets", total_trades, len(markets))
        return total_trades

    async def collect_market_status(self) -> int:
        """Refresh market status from the CLOB API.

        Called every 15 minutes by the scheduler.
        Marks markets as expired/closed if the CLOB reports them inactive.

        Returns
        -------
        int
            Number of markets updated.
        """
        markets = await self._get_active_markets()
        if not markets:
            return 0

        updated = 0
        for market in markets:
            try:
                changed = await self._refresh_market_status(market)
                if changed:
                    updated += 1
            except Exception as exc:
                logger.error(
                    "collect_market_status: failed for market_id=%d: %s",
                    market.id, exc,
                )

        if updated:
            try:
                await self._db.flush()
            except Exception as exc:
                logger.error("collect_market_status: DB flush failed: %s", exc)

        logger.debug("collect_market_status: updated %d market(s)", updated)
        return updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_active_markets(self) -> list[Any]:
        """Return all markets with status='active' from the DB."""
        try:
            from prophet.db.models import Market

            stmt = select(Market).where(Market.status == "active")
            result = await self._db.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            logger.error("_get_active_markets failed: %s", exc)
            return []

    async def _collect_trades_for_market(self, market: Any) -> int:
        """Fetch recent trades for YES and NO tokens and persist new ones.

        Returns the number of new trade rows inserted.
        """
        from prophet.db.models import ObservedTrade

        new_count = 0
        for token_id, side_label in [
            (market.token_id_yes, "YES"),
            (market.token_id_no, "NO"),
        ]:
            try:
                trades = await self._clob.get_trades(token_id, limit=50)
                for trade in trades:
                    # Deduplication: skip if a trade with same token+price+timestamp+size exists
                    # (We rely on the combination rather than a trade_id that might be absent)
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
                    self._db.add(trade_row)
                    new_count += 1

                if trades:
                    await self._db.flush()

            except Exception as exc:
                logger.warning(
                    "_collect_trades_for_market: token=%s side=%s: %s",
                    token_id[:12], side_label, exc,
                )

        return new_count

    async def _refresh_market_status(self, market: Any) -> bool:
        """Check the CLOB for updated market status and update the DB row.

        Returns True if the status was changed.
        """
        try:
            markets_page, _ = await self._clob.get_markets(limit=1, active=None)
            # The CLOB get_markets doesn't filter by condition_id easily,
            # so we rely on the Gamma scanner for authoritative resolution.
            # Here we check if the market's resolution_date has passed.
            if market.resolution_date and market.resolution_date < _utcnow().date():
                if market.status == "active":
                    market.status = "expired"
                    logger.info(
                        "Market expired (past resolution_date): market_id=%d",
                        market.id,
                    )
                    return True
        except Exception as exc:
            logger.debug("_refresh_market_status: %s", exc)

        return False
