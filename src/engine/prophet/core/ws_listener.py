"""
WebSocket real-time price listener for Polymarket's market feed.

Maintains a persistent WebSocket connection to Polymarket and keeps an
in-memory price cache updated in real time.  Other components
(DataCollector, SignalGenerator) can call ``get_price`` / ``get_mid``
without hitting the REST API.

Usage
-----
    listener = PolymarketWSListener()
    await listener.start()          # call from app lifespan startup
    price = listener.get_price(token_id)   # returns dict or None
    mid   = listener.get_mid(token_id)     # returns float or None
    await listener.stop()           # call from app lifespan shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_PING_INTERVAL = 25   # seconds
_PING_TIMEOUT = 10    # seconds
_RECONNECT_DELAY = 5  # seconds on error


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PolymarketWSListener:
    """
    Maintains a persistent WebSocket connection to Polymarket's real-time
    market feed. Updates an in-memory price cache that other components
    (DataCollector, SignalGenerator) can read without hitting the REST API.

    Usage::

        listener = PolymarketWSListener()
        await listener.start()              # call from app startup
        price = listener.get_price(token_id)  # returns dict or None
        await listener.stop()               # call from app shutdown
    """

    def __init__(self) -> None:
        # token_id → {best_bid, best_ask, mid, ts}
        self._prices: dict[str, dict[str, Any]] = {}
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._token_ids: list[str] = []
        self._ws: Any = None  # active websockets connection

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load active market token IDs from DB and start the background task."""
        if self._running:
            logger.warning("PolymarketWSListener is already running")
            return

        await self._load_token_ids()
        self._running = True
        self._task = asyncio.create_task(self._run_forever(), name="ws_listener")
        logger.info(
            "PolymarketWSListener started — subscribing to %d token(s)",
            len(self._token_ids),
        )

    async def stop(self) -> None:
        """Stop the listener and cancel the background task."""
        if not self._running:
            return

        self._running = False

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("PolymarketWSListener stopped.")

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def get_price(self, token_id: str) -> dict[str, Any] | None:
        """Return the cached price dict for a token, or None if not cached.

        The returned dict has keys: ``best_bid``, ``best_ask``, ``mid``, ``ts``.
        """
        return self._prices.get(token_id)

    def get_mid(self, token_id: str) -> float | None:
        """Return the mid price for a token, or None if not cached."""
        entry = self._prices.get(token_id)
        if entry is None:
            return None
        return entry.get("mid")

    async def refresh_subscriptions(self) -> None:
        """Reload token IDs from DB and re-send the subscription message.

        Call this after new markets are added so the listener starts
        receiving price updates for them.
        """
        await self._load_token_ids()
        if self._ws is not None:
            try:
                await self._send_subscription(self._ws)
                logger.info(
                    "PolymarketWSListener re-subscribed to %d token(s)",
                    len(self._token_ids),
                )
            except Exception as exc:
                logger.warning("refresh_subscriptions: failed to re-subscribe: %s", exc)

    # ------------------------------------------------------------------
    # Monitoring properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True if there is an active WebSocket connection."""
        return self._ws is not None and self._running

    @property
    def price_count(self) -> int:
        """Number of tokens currently in the price cache."""
        return len(self._prices)

    # ------------------------------------------------------------------
    # Internal — connection management
    # ------------------------------------------------------------------

    async def _run_forever(self) -> None:
        """Outer reconnect loop — catches all errors and reconnects after delay."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("PolymarketWSListener task cancelled.")
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.warning(
                    "PolymarketWSListener connection lost (%s: %s) — reconnecting in %ds",
                    type(exc).__name__, exc, _RECONNECT_DELAY,
                )
                self._ws = None
                await asyncio.sleep(_RECONNECT_DELAY)

        logger.info("PolymarketWSListener _run_forever exited.")

    async def _connect_and_listen(self) -> None:
        """Connect to the WebSocket, subscribe, and read messages until closed."""
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "websockets library is required: pip install websockets"
            ) from exc

        logger.info("PolymarketWSListener connecting to %s …", _WS_URL)

        async with websockets.connect(
            _WS_URL,
            ping_interval=_PING_INTERVAL,
            ping_timeout=_PING_TIMEOUT,
        ) as ws:
            self._ws = ws
            logger.info("PolymarketWSListener connected.")

            await self._send_subscription(ws)

            async for raw in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.debug("PolymarketWSListener: JSON parse error: %s — raw=%r", exc, raw[:200])
                    continue

                self._handle_message(data)

        self._ws = None
        logger.info("PolymarketWSListener WebSocket closed.")

    async def _send_subscription(self, ws: Any) -> None:
        """Send the market subscription message for all known token IDs."""
        if not self._token_ids:
            logger.warning("PolymarketWSListener: no token IDs to subscribe to")
            return

        msg = json.dumps({
            "assets_ids": self._token_ids,
            "type": "market",
        })
        await ws.send(msg)
        logger.debug(
            "PolymarketWSListener: subscription sent for %d token(s)", len(self._token_ids)
        )

    # ------------------------------------------------------------------
    # Internal — DB loader
    # ------------------------------------------------------------------

    async def _load_token_ids(self) -> None:
        """Query DB for all active market token IDs."""
        try:
            from sqlalchemy import select
            from prophet.db.database import get_session
            from prophet.db.models import Market

            async with get_session() as session:
                result = await session.execute(
                    select(Market.token_id_yes, Market.token_id_no)
                    .where(Market.status == "active")
                )
                rows = result.all()

            token_ids: list[str] = []
            for yes_id, no_id in rows:
                if yes_id:
                    token_ids.append(yes_id)
                if no_id:
                    token_ids.append(no_id)

            self._token_ids = token_ids
            logger.info(
                "PolymarketWSListener: loaded %d token IDs from %d active markets",
                len(token_ids), len(rows),
            )
        except Exception as exc:
            logger.error("PolymarketWSListener: failed to load token IDs: %s", exc)
            # Keep existing token_ids if reload fails

    # ------------------------------------------------------------------
    # Internal — message dispatch
    # ------------------------------------------------------------------

    def _handle_message(self, data: Any) -> None:
        """Dispatch incoming message by event_type."""
        if not isinstance(data, dict):
            return

        event_type = data.get("event_type")

        if event_type == "price_change":
            self._handle_price_change(data)
        elif event_type == "book":
            self._handle_book(data)
        elif event_type == "last_trade_price":
            self._handle_last_trade(data)
        else:
            logger.debug("PolymarketWSListener: unhandled event_type=%r", event_type)

    def _handle_price_change(self, data: dict) -> None:
        """Handle price_change events — update bid/ask/mid for each changed asset."""
        changes = data.get("price_changes", [])
        for change in changes:
            asset_id = change.get("asset_id")
            if not asset_id:
                continue

            try:
                best_bid = float(change["best_bid"]) if change.get("best_bid") else None
                best_ask = float(change["best_ask"]) if change.get("best_ask") else None
            except (ValueError, TypeError) as exc:
                logger.debug("_handle_price_change: parse error for asset=%s: %s", asset_id, exc)
                continue

            mid = None
            if best_bid is not None and best_ask is not None:
                mid = (best_bid + best_ask) / 2.0

            entry = self._prices.get(asset_id, {})
            entry["best_bid"] = best_bid
            entry["best_ask"] = best_ask
            if mid is not None:
                entry["mid"] = mid
            entry["ts"] = _utcnow()
            self._prices[asset_id] = entry

            logger.debug(
                "price_change: asset=%s bid=%s ask=%s mid=%s",
                asset_id[:12], best_bid, best_ask, mid,
            )

    def _handle_book(self, data: dict) -> None:
        """Handle book (full order book snapshot) events — update from top of book."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        try:
            best_bid = float(bids[0]["price"]) if bids else None
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            logger.debug("_handle_book: bid parse error for asset=%s: %s", asset_id, exc)
            best_bid = None

        try:
            best_ask = float(asks[0]["price"]) if asks else None
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            logger.debug("_handle_book: ask parse error for asset=%s: %s", asset_id, exc)
            best_ask = None

        mid = None
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0

        entry = self._prices.get(asset_id, {})
        entry["best_bid"] = best_bid
        entry["best_ask"] = best_ask
        if mid is not None:
            entry["mid"] = mid
        entry["ts"] = _utcnow()
        self._prices[asset_id] = entry

        logger.debug(
            "book snapshot: asset=%s bid=%s ask=%s mid=%s",
            asset_id[:12], best_bid, best_ask, mid,
        )

    def _handle_last_trade(self, data: dict) -> None:
        """Handle last_trade_price events — update mid from the last traded price."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return

        try:
            price = float(data["price"]) if data.get("price") else None
        except (ValueError, TypeError) as exc:
            logger.debug("_handle_last_trade: parse error for asset=%s: %s", asset_id, exc)
            return

        if price is None:
            return

        entry = self._prices.get(asset_id, {})
        entry["mid"] = price
        entry["ts"] = _utcnow()
        self._prices[asset_id] = entry

        logger.debug("last_trade: asset=%s price=%s", asset_id[:12], price)
