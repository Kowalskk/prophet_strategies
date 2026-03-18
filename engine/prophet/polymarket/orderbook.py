"""
Order book fetching, metric computation, and snapshot persistence.

:class:`OrderBookService` is the main entry point.  It:

1. Fetches YES and NO order books for each active market via
   :class:`~prophet.polymarket.clob_client.PolymarketClient`.
2. Computes derived metrics: ``best_bid``, ``best_ask``, ``spread_pct``,
   ``bid_depth_10pct``, ``ask_depth_10pct``, and ``mid_price``.
3. Persists a snapshot row to the ``orderbook_snapshots`` table.
4. Optionally caches the latest snapshot in Redis (key format:
   ``ob:{market_id}:{side}``).

Metric definitions
------------------
- ``best_bid``       — highest bid price
- ``best_ask``       — lowest ask price
- ``spread_pct``     — ``(best_ask - best_bid) / best_ask * 100``
- ``bid_depth_10pct``— total USD available in bids within 10 % of ``best_bid``
  (i.e. all bids with price >= ``best_bid * 0.90``)
- ``ask_depth_10pct``— total USD available in asks within 10 % of ``best_ask``
  (i.e. all asks with price <= ``best_ask * 1.10``)
- ``mid_price``      — ``(best_bid + best_ask) / 2``

Depth is expressed in USD (price × size for each level within the range).

Usage
-----
    service = OrderBookService(clob_client, db_session)
    snapshot = await service.snapshot_market(market_id, token_id_yes, "yes")
    # Returns an OrderBook with all metrics populated and a DB row persisted.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from prophet.polymarket.clob_client import PolymarketClient
from prophet.polymarket.models import OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure metric computation (no I/O — easy to test in isolation)
# ---------------------------------------------------------------------------


def compute_metrics(book: OrderBook, depth_band_pct: float = 10.0) -> OrderBook:
    """Compute and attach derived metrics to an :class:`OrderBook` in place.

    Mutates and returns the same object so callers can chain:
    ``book = compute_metrics(raw_book)``.

    Parameters
    ----------
    book:
        The order book to process.  ``bids`` must be sorted descending,
        ``asks`` must be sorted ascending (enforced by
        :meth:`~prophet.polymarket.clob_client.PolymarketClient.get_order_book`).
    depth_band_pct:
        Percentage band around the best price for depth calculation.
        Default is 10 % (``bid_depth_10pct`` / ``ask_depth_10pct``).

    Returns
    -------
    OrderBook
        The same object with ``best_bid``, ``best_ask``, ``spread_pct``,
        ``bid_depth_10pct``, ``ask_depth_10pct``, and ``mid_price`` set.
    """
    # Best prices
    best_bid: float | None = book.bids[0].price if book.bids else None
    best_ask: float | None = book.asks[0].price if book.asks else None

    book.best_bid = best_bid
    book.best_ask = best_ask

    # Spread
    if best_bid is not None and best_ask is not None and best_ask > 0:
        book.spread_pct = (best_ask - best_bid) / best_ask * 100.0
    else:
        book.spread_pct = None

    # Mid price
    if best_bid is not None and best_ask is not None:
        book.mid_price = (best_bid + best_ask) / 2.0
    else:
        book.mid_price = None

    # Depth within `depth_band_pct` % of best price
    book.bid_depth_10pct = _compute_depth(
        levels=book.bids,
        reference_price=best_bid,
        band_pct=depth_band_pct,
        direction="bid",
    )
    book.ask_depth_10pct = _compute_depth(
        levels=book.asks,
        reference_price=best_ask,
        band_pct=depth_band_pct,
        direction="ask",
    )

    return book


def _compute_depth(
    levels: list[OrderBookLevel],
    reference_price: float | None,
    band_pct: float,
    direction: str,  # "bid" or "ask"
) -> float:
    """Sum USD depth within ``band_pct`` % of ``reference_price``.

    For bids: include all levels with ``price >= reference_price * (1 - band_pct/100)``.
    For asks: include all levels with ``price <= reference_price * (1 + band_pct/100)``.

    Each level's USD contribution is ``level.price * level.size``.
    """
    if reference_price is None or not levels:
        return 0.0

    band = band_pct / 100.0
    total = 0.0

    if direction == "bid":
        threshold = reference_price * (1.0 - band)
        for level in levels:
            if level.price < threshold:
                break  # bids are descending; once below threshold we're done
            total += level.price * level.size
    else:
        threshold = reference_price * (1.0 + band)
        for level in levels:
            if level.price > threshold:
                break  # asks are ascending; once above threshold we're done
            total += level.price * level.size

    return round(total, 6)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrderBookService:
    """Fetches, computes, and persists order book snapshots.

    Parameters
    ----------
    clob_client:
        An already-started :class:`~prophet.polymarket.clob_client.PolymarketClient`.
    db_session:
        An SQLAlchemy async session (``AsyncSession``).  May be None when the
        service is used read-only (metrics only, no DB persistence).
    redis_client:
        An async Redis client (``redis.asyncio.Redis``).  May be None when
        Redis caching is not required.
    depth_band_pct:
        Band width for depth calculations (default 10 %).
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        db_session: Any | None = None,
        redis_client: Any | None = None,
        depth_band_pct: float = 10.0,
    ) -> None:
        self._clob = clob_client
        self._db = db_session
        self._redis = redis_client
        self._depth_band_pct = depth_band_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_and_compute(self, token_id: str) -> OrderBook:
        """Fetch the live order book for ``token_id`` and compute metrics.

        Returns an :class:`OrderBook` with all derived fields populated.
        Does NOT persist to DB — use :meth:`snapshot_market` for that.
        """
        book = await self._clob.get_order_book(token_id)
        return compute_metrics(book, self._depth_band_pct)

    async def snapshot_market(
        self,
        market_id: int,
        token_id: str,
        side: str,
    ) -> OrderBook:
        """Fetch, compute, persist (DB + Redis) a snapshot for one token.

        Parameters
        ----------
        market_id:
            DB primary key of the parent market row.
        token_id:
            CLOB token ID (YES or NO side).
        side:
            ``"yes"`` or ``"no"``.

        Returns
        -------
        OrderBook
            The computed order book (metrics populated).
        """
        side = side.lower()
        book = await self.fetch_and_compute(token_id)

        if self._db is not None:
            await self._persist_snapshot(market_id, token_id, side, book)

        if self._redis is not None:
            await self._cache_snapshot(market_id, side, book)

        return book

    async def snapshot_both_sides(
        self,
        market_id: int,
        token_id_yes: str,
        token_id_no: str,
    ) -> dict[str, OrderBook]:
        """Snapshot YES and NO sides concurrently.

        Returns
        -------
        dict
            ``{"yes": OrderBook, "no": OrderBook}``
        """
        import asyncio

        yes_book, no_book = await asyncio.gather(
            self.snapshot_market(market_id, token_id_yes, "yes"),
            self.snapshot_market(market_id, token_id_no, "no"),
        )
        return {"yes": yes_book, "no": no_book}

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    async def _persist_snapshot(
        self,
        market_id: int,
        token_id: str,
        side: str,
        book: OrderBook,
    ) -> None:
        """Insert an ``orderbook_snapshots`` row for this book state."""
        try:
            from prophet.db.models import OrderBookSnapshot

            raw_book: dict[str, Any] = {
                "bids": [{"price": l.price, "size": l.size} for l in book.bids],
                "asks": [{"price": l.price, "size": l.size} for l in book.asks],
            }

            snapshot = OrderBookSnapshot(
                market_id=market_id,
                token_id=token_id,
                side=side,
                timestamp=book.timestamp,
                best_bid=book.best_bid,
                best_ask=book.best_ask,
                bid_depth_10pct=book.bid_depth_10pct,
                ask_depth_10pct=book.ask_depth_10pct,
                spread_pct=book.spread_pct,
                raw_book=raw_book,
            )
            self._db.add(snapshot)
            await self._db.flush()
            logger.debug(
                "Persisted OB snapshot market_id=%d side=%s bid=%.4f ask=%.4f",
                market_id, side, book.best_bid or 0, book.best_ask or 0,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist OB snapshot market_id=%d side=%s: %s",
                market_id, side, exc,
            )
            raise

    # ------------------------------------------------------------------
    # Redis caching
    # ------------------------------------------------------------------

    async def _cache_snapshot(
        self,
        market_id: int,
        side: str,
        book: OrderBook,
    ) -> None:
        """Cache the latest order book in Redis for fast dashboard reads.

        Key: ``ob:{market_id}:{side}``
        TTL: 10 minutes (covers at least 1 full scan cycle)
        """
        key = f"ob:{market_id}:{side}"
        payload: dict[str, Any] = {
            "token_id": book.token_id,
            "timestamp": book.timestamp.isoformat(),
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "mid_price": book.mid_price,
            "spread_pct": book.spread_pct,
            "bid_depth_10pct": book.bid_depth_10pct,
            "ask_depth_10pct": book.ask_depth_10pct,
            "bids": [{"price": l.price, "size": l.size} for l in book.bids[:20]],
            "asks": [{"price": l.price, "size": l.size} for l in book.asks[:20]],
        }
        try:
            await self._redis.setex(key, 600, json.dumps(payload))
            logger.debug("Cached OB in Redis: %s", key)
        except Exception as exc:
            # Redis failure should NOT block the snapshot pipeline
            logger.warning("Redis cache write failed for %s: %s", key, exc)

    # ------------------------------------------------------------------
    # Redis reads (for dashboard / strategies)
    # ------------------------------------------------------------------

    async def get_cached_book(
        self,
        market_id: int,
        side: str,
    ) -> OrderBook | None:
        """Read the latest cached order book from Redis.

        Returns None if not cached or Redis is unavailable.
        """
        if self._redis is None:
            return None

        key = f"ob:{market_id}:{side}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            payload = json.loads(raw)

            bids = [OrderBookLevel(price=l["price"], size=l["size"]) for l in payload.get("bids", [])]
            asks = [OrderBookLevel(price=l["price"], size=l["size"]) for l in payload.get("asks", [])]

            return OrderBook(
                token_id=payload["token_id"],
                bids=bids,
                asks=asks,
                timestamp=datetime.fromisoformat(payload["timestamp"]),
                best_bid=payload.get("best_bid"),
                best_ask=payload.get("best_ask"),
                mid_price=payload.get("mid_price"),
                spread_pct=payload.get("spread_pct"),
                bid_depth_10pct=payload.get("bid_depth_10pct", 0.0),
                ask_depth_10pct=payload.get("ask_depth_10pct", 0.0),
            )
        except Exception as exc:
            logger.warning("Redis cache read failed for %s: %s", key, exc)
            return None

    # ------------------------------------------------------------------
    # Imbalance metric (bonus — useful for liquidity sniper)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_book_imbalance(book: OrderBook) -> float | None:
        """Compute order book imbalance in [-1, 1].

        Positive → more bid pressure.
        Negative → more ask pressure.
        Returns None if either side is empty.

        Formula: ``(bid_depth - ask_depth) / (bid_depth + ask_depth)``
        where depth is ``bid_depth_10pct`` and ``ask_depth_10pct``.
        """
        bid_d = book.bid_depth_10pct
        ask_d = book.ask_depth_10pct
        total = bid_d + ask_d
        if total == 0.0:
            return None
        return (bid_d - ask_d) / total
