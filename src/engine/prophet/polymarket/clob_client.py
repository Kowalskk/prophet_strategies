"""
Polymarket CLOB API client.

Wraps the CLOB REST API (https://clob.polymarket.com) for read operations and,
when live trading is enabled, order placement via py-clob-client.

Design decisions
----------------
* **Paper trading guard** — :meth:`PolymarketClient.place_order` raises
  ``RuntimeError`` when ``settings.paper_trading`` is True.  This is a hard
  block that cannot be bypassed without explicitly disabling paper mode in the
  .env file.
* **httpx.AsyncClient** is used for all direct HTTP calls so the client is
  non-blocking inside FastAPI / APScheduler async contexts.
* **py-clob-client** is used for authenticated order operations (place, cancel,
  list open orders) when live trading is active.  For read-only operations we
  call the REST API directly to avoid dependency on the SDK's blocking HTTP
  layer.
* **Retries** — transient 5xx errors and network timeouts are retried up to
  ``_MAX_RETRIES`` times with exponential back-off.

Usage
-----
    from prophet.polymarket.clob_client import PolymarketClient

    client = PolymarketClient()
    markets = await client.get_markets(limit=100)
    book = await client.get_order_book("0xabc123...")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from prophet.config import settings
from prophet.polymarket.models import MarketInfo, OrderBook, OrderBookLevel, Trade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLOB_BASE_URL = "https://clob.polymarket.com"
DATA_API_BASE_URL = "https://data-api.polymarket.com"
_DEFAULT_TIMEOUT = 15.0  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds — multiplied by attempt number
_BATCH_BOOK_SIZE = 50  # max token_ids per POST /books request


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any] | list[Any]:
    """Perform an HTTP request with retries on transient failures.

    Retries on:
    - Network errors (httpx.RequestError)
    - 429 Too Many Requests
    - 5xx Server Errors

    Returns the parsed JSON response body.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", _RETRY_BACKOFF * attempt))
                logger.warning("Rate limited by CLOB API; sleeping %.1fs (attempt %d)", retry_after, attempt)
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            last_exc = exc
            logger.warning("Network error on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
            await asyncio.sleep(_RETRY_BACKOFF * attempt)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                logger.warning(
                    "CLOB API 5xx (attempt %d/%d): %s", attempt, _MAX_RETRIES, exc.response.text
                )
                await asyncio.sleep(_RETRY_BACKOFF * attempt)
            else:
                raise  # 4xx errors are not retried
    raise RuntimeError(f"CLOB request failed after {_MAX_RETRIES} attempts") from last_exc


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PolymarketClient:
    """Async client for the Polymarket CLOB API.

    Read operations (markets, order books, trades) use httpx directly.
    Order placement / cancellation uses py-clob-client when live trading is
    enabled.

    Parameters
    ----------
    base_url:
        Override for the CLOB base URL (useful for testing).
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = CLOB_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._data_http: httpx.AsyncClient | None = None  # Data API client

        # Lazily initialised py-clob-client (only needed for live orders)
        self._clob_sdk: Any | None = None

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PolymarketClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def start(self) -> None:
        """Open the shared httpx sessions (CLOB + Data API)."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
        if self._data_http is None:
            self._data_http = httpx.AsyncClient(
                base_url=DATA_API_BASE_URL,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
        logger.debug("PolymarketClient HTTP sessions opened (CLOB + Data API)")

    async def close(self) -> None:
        """Close all httpx sessions."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._data_http is not None:
            await self._data_http.aclose()
            self._data_http = None
        logger.debug("PolymarketClient HTTP sessions closed")

    def _ensure_started(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "PolymarketClient is not started. "
                "Use 'async with PolymarketClient() as client:' or call await client.start() first."
            )
        return self._http

    # ------------------------------------------------------------------
    # Internal SDK initialisation
    # ------------------------------------------------------------------

    def _get_sdk_client(self) -> Any:
        """Return a py-clob-client ClobClient instance for live order ops.

        Initialised lazily on first call so that import errors are surfaced
        clearly rather than at module load time.
        """
        if self._clob_sdk is not None:
            return self._clob_sdk

        try:
            from py_clob_client.client import ClobClient  # type: ignore[import]
            from py_clob_client.clob_types import ApiCreds  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "py-clob-client is not installed. Run: pip install py-clob-client"
            ) from exc

        if not settings.private_key:
            raise ValueError(
                "PRIVATE_KEY is not set in .env. "
                "A Polygon wallet private key is required for live order signing."
            )

        creds = ApiCreds(
            api_key=settings.polymarket_api_key,
            api_secret=settings.polymarket_secret,
            api_passphrase=settings.polymarket_passphrase,
        )

        self._clob_sdk = ClobClient(
            host=self._base_url,
            chain_id=settings.chain_id,
            key=settings.private_key,
            creds=creds,
        )
        logger.info("py-clob-client SDK initialised (chain_id=%d)", settings.chain_id)
        return self._clob_sdk

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    async def get_markets(
        self,
        *,
        limit: int = 500,
        next_cursor: str | None = None,
        active: bool | None = None,
    ) -> tuple[list[MarketInfo], str | None]:
        """Fetch a page of markets from the CLOB API.

        Parameters
        ----------
        limit:
            Maximum number of markets per page (CLOB cap: 500).
        next_cursor:
            Pagination cursor from a previous call.  None = first page.
        active:
            If True, filter to active markets only.

        Returns
        -------
        (markets, next_cursor)
            ``next_cursor`` is None when there are no more pages.
        """
        http = self._ensure_started()
        params: dict[str, Any] = {"limit": limit}
        if next_cursor:
            params["next_cursor"] = next_cursor

        data = await _request_with_retry(http, "GET", "/markets", params=params)

        if isinstance(data, dict):
            raw_markets = data.get("data", [])
            cursor = data.get("next_cursor")
            if cursor in ("", "LTE=", None):
                cursor = None
        else:
            raw_markets = data  # some older endpoints return a bare list
            cursor = None

        markets: list[MarketInfo] = []
        for raw in raw_markets:
            try:
                if active is not None and raw.get("active") != active:
                    continue
                markets.append(_parse_market_info(raw))
            except Exception as exc:
                logger.debug("Skipping malformed market record: %s — %s", raw.get("condition_id"), exc)

        return markets, cursor

    async def get_market_resolution(self, condition_id: str) -> tuple[bool, str | None]:
        """Check if a market has resolved by querying CLOB tokens for a winner.

        Returns (resolved, outcome) where outcome is "YES", "NO", or None.
        CLOB sets token.winner=True immediately when market resolves, unlike Gamma.
        """
        http = self._ensure_started()
        try:
            data = await _request_with_retry(http, "GET", f"/markets/{condition_id}")
            tokens = data.get("tokens", []) if isinstance(data, dict) else []
            for token in tokens:
                if isinstance(token, dict) and token.get("winner") is True:
                    return True, str(token.get("outcome", "")).upper() or None
        except Exception as exc:
            logger.debug("CLOB resolution check failed for %s: %s", condition_id[:12], exc)
        return False, None

    async def get_all_markets(self, *, active: bool | None = None) -> list[MarketInfo]:
        """Paginate through all CLOB markets and return the full list.

        Caution: this may take several seconds and return thousands of records.
        """
        all_markets: list[MarketInfo] = []
        cursor: str | None = None
        page = 0

        while True:
            page += 1
            batch, cursor = await self.get_markets(limit=500, next_cursor=cursor, active=active)
            all_markets.extend(batch)
            logger.debug("Fetched page %d: %d markets (cursor=%s)", page, len(batch), cursor)

            if cursor is None:
                break
            if page > 200:  # safety — avoid infinite loops
                logger.warning("Stopped pagination after 200 pages (sanity limit)")
                break

        logger.info("get_all_markets: %d total markets fetched", len(all_markets))
        return all_markets

    # ------------------------------------------------------------------
    # Order book
    # ------------------------------------------------------------------

    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch the current order book for a single token ID.

        The raw response is parsed into an :class:`~prophet.polymarket.models.OrderBook`
        with bids sorted descending and asks sorted ascending.  Derived metrics
        (best_bid, best_ask, spread_pct, depth) are NOT computed here — call
        :func:`~prophet.polymarket.orderbook.compute_metrics` separately.
        """
        http = self._ensure_started()
        params = {"token_id": token_id}
        data = await _request_with_retry(http, "GET", "/book", params=params)

        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []

        if isinstance(data, dict):
            for level in data.get("bids", []):
                try:
                    bids.append(OrderBookLevel(price=level["price"], size=level["size"]))
                except Exception:
                    pass
            for level in data.get("asks", []):
                try:
                    asks.append(OrderBookLevel(price=level["price"], size=level["size"]))
                except Exception:
                    pass

        # Ensure proper sort order
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return OrderBook(
            token_id=token_id,
            bids=bids,
            asks=asks,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def get_trades(
        self,
        token_id: str,
        *,
        limit: int = 100,
    ) -> list[Trade]:
        """Fetch recent public trades for a token via the Data API (no auth).

        Uses ``https://data-api.polymarket.com/trades`` which is public and
        does NOT require HMAC authentication (unlike ``/data/trades`` on the CLOB).

        Parameters
        ----------
        token_id:
            CLOB token ID to query.
        limit:
            Maximum number of trades to return (Data API max: 10000).
        """
        if self._data_http is None:
            await self.start()
        params: dict[str, Any] = {"asset_id": token_id, "limit": min(limit, 10000)}

        try:
            data = await _request_with_retry(self._data_http, "GET", "/trades", params=params)  # type: ignore[arg-type]
        except Exception as exc:
            logger.debug("Data API trades failed for token %s: %s", token_id[:16], exc)
            return []

        raw_trades = data if isinstance(data, list) else data.get("data", [])

        trades: list[Trade] = []
        for raw in raw_trades:
            try:
                trades.append(_parse_trade(raw))
            except Exception as exc:
                logger.debug("Skipping malformed trade: %s", exc)

        return trades

    # ------------------------------------------------------------------
    # Prices (mid price)
    # ------------------------------------------------------------------

    async def get_price(self, token_id: str) -> dict[str, float | None]:
        """Return the current best bid, best ask and mid price for a token.

        Returns a dict with keys: ``bid``, ``ask``, ``mid``.
        Any value may be None if that side of the book is empty.
        """
        book = await self.get_order_book(token_id)
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        mid: float | None = None
        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2.0
        return {"bid": best_bid, "ask": best_ask, "mid": mid}

    # ------------------------------------------------------------------
    # Batch endpoints (reduce N HTTP calls → 1)
    # ------------------------------------------------------------------

    async def get_order_books_batch(
        self, token_ids: list[str],
    ) -> dict[str, OrderBook]:
        """Fetch order books for multiple tokens in batch via ``POST /books``.

        Returns a dict mapping token_id → OrderBook.  Token IDs that fail
        (e.g. 404 no liquidity) are silently skipped.
        """
        http = self._ensure_started()
        results: dict[str, OrderBook] = {}

        # CLOB POST /books expects [{"token_id": "..."}, ...] format
        for i in range(0, len(token_ids), _BATCH_BOOK_SIZE):
            chunk = token_ids[i : i + _BATCH_BOOK_SIZE]
            payload = [{"token_id": tid} for tid in chunk]
            try:
                data = await _request_with_retry(
                    http, "POST", "/books",
                    json=payload,
                )
                if not isinstance(data, list):
                    data = [data]

                for book_data in data:
                    if not isinstance(book_data, dict):
                        continue
                    tid = book_data.get("asset_id", book_data.get("token_id", ""))
                    if not tid:
                        # Try to match by position
                        continue

                    bids: list[OrderBookLevel] = []
                    asks: list[OrderBookLevel] = []
                    for level in book_data.get("bids", []):
                        try:
                            bids.append(OrderBookLevel(price=level["price"], size=level["size"]))
                        except Exception:
                            pass
                    for level in book_data.get("asks", []):
                        try:
                            asks.append(OrderBookLevel(price=level["price"], size=level["size"]))
                        except Exception:
                            pass

                    bids.sort(key=lambda x: x.price, reverse=True)
                    asks.sort(key=lambda x: x.price)

                    results[tid] = OrderBook(
                        token_id=tid,
                        bids=bids,
                        asks=asks,
                        timestamp=_utcnow(),
                    )
            except Exception as exc:
                logger.warning(
                    "Batch OB fetch failed for chunk of %d tokens: %s", len(chunk), exc,
                )

        return results

    async def get_midpoints_batch(
        self, token_ids: list[str],
    ) -> dict[str, float]:
        """Fetch midpoint prices for multiple tokens via ``POST /midpoints``.

        Returns a dict mapping token_id → mid_price.
        """
        http = self._ensure_started()
        results: dict[str, float] = {}
        try:
            data = await _request_with_retry(
                http, "POST", "/midpoints",
                json=token_ids,
            )
            if isinstance(data, dict):
                for tid, price in data.items():
                    try:
                        results[tid] = float(price)
                    except (TypeError, ValueError):
                        pass
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        tid = item.get("token_id", item.get("asset_id", ""))
                        mid = item.get("mid", item.get("midpoint"))
                        if tid and mid is not None:
                            results[tid] = float(mid)
        except Exception as exc:
            logger.warning("Batch midpoints failed: %s", exc)
        return results

    async def get_last_trade_prices_batch(
        self, token_ids: list[str],
    ) -> dict[str, float]:
        """Fetch last trade prices for multiple tokens via ``POST /last-trades-prices``.

        No authentication required. Returns token_id → price.
        """
        http = self._ensure_started()
        results: dict[str, float] = {}
        try:
            data = await _request_with_retry(
                http, "POST", "/last-trades-prices",
                json=token_ids,
            )
            if isinstance(data, dict):
                for tid, price in data.items():
                    try:
                        results[tid] = float(price)
                    except (TypeError, ValueError):
                        pass
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        tid = item.get("token_id", item.get("asset_id", ""))
                        price = item.get("price", item.get("last_trade_price"))
                        if tid and price is not None:
                            results[tid] = float(price)
        except Exception as exc:
            logger.warning("Batch last trade prices failed: %s", exc)
        return results

    async def get_spreads_batch(
        self, token_ids: list[str],
    ) -> dict[str, dict[str, float]]:
        """Fetch spreads for multiple tokens via ``POST /spreads``.

        Returns token_id → {bid, ask, spread}.
        """
        http = self._ensure_started()
        results: dict[str, dict[str, float]] = {}
        try:
            data = await _request_with_retry(
                http, "POST", "/spreads",
                json=token_ids,
            )
            if isinstance(data, dict):
                for tid, spread_data in data.items():
                    if isinstance(spread_data, dict):
                        results[tid] = {
                            "bid": float(spread_data.get("bid", 0)),
                            "ask": float(spread_data.get("ask", 0)),
                            "spread": float(spread_data.get("spread", 0)),
                        }
        except Exception as exc:
            logger.warning("Batch spreads failed: %s", exc)
        return results

    # ------------------------------------------------------------------
    # Order placement (LIVE ONLY)
    # ------------------------------------------------------------------

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> str:
        """Place a limit order on the CLOB.

        Parameters
        ----------
        token_id:
            CLOB token ID.
        side:
            "BUY" or "SELL".
        price:
            Limit price per share in [0, 1].
        size:
            Number of shares to trade.

        Returns
        -------
        str
            Order ID returned by the CLOB.

        Raises
        ------
        RuntimeError
            Always raised when ``settings.paper_trading`` is True.
        ValueError
            If side is not "BUY" or "SELL".
        """
        if settings.paper_trading:
            raise RuntimeError(
                "Paper trading mode is ON. Real order placement is disabled. "
                "Set PAPER_TRADING=false in .env to enable live trading (requires >= 8 weeks validation)."
            )

        if side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side {side!r}. Must be 'BUY' or 'SELL'.")

        sdk = self._get_sdk_client()

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore[import]

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side.upper(),
                fee_rate_bps=0,
                nonce=0,
                expiration=0,
            )
            signed_order = sdk.create_order(order_args)
            resp = sdk.post_order(signed_order, OrderType.GTC)
            order_id: str = resp.get("orderID", "")
            logger.info(
                "Order placed: id=%s token=%s side=%s price=%.4f size=%.2f",
                order_id, token_id, side, price, size,
            )
            return order_id
        except Exception as exc:
            logger.error("Order placement failed: %s", exc)
            raise

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by ID.

        Raises
        ------
        RuntimeError
            When ``settings.paper_trading`` is True.
        """
        if settings.paper_trading:
            raise RuntimeError(
                "Paper trading mode is ON. Real order cancellation is disabled."
            )

        sdk = self._get_sdk_client()
        try:
            resp = sdk.cancel(order_id)
            cancelled: bool = resp.get("cancelled", False)
            logger.info("Order cancelled: id=%s success=%s", order_id, cancelled)
            return bool(cancelled)
        except Exception as exc:
            logger.error("Order cancellation failed for %s: %s", order_id, exc)
            raise

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch a single order's status from the CLOB.

        Requires L2 HMAC auth — uses the SDK client.
        Returns a dict with: id, status, size_matched, original_size, price, asset_id.

        Raises
        ------
        RuntimeError
            When ``settings.paper_trading`` is True.
        """
        if settings.paper_trading:
            raise RuntimeError("Paper trading mode is ON.")

        sdk = self._get_sdk_client()
        try:
            resp = sdk.get_order(order_id)
            return resp if isinstance(resp, dict) else {}
        except Exception as exc:
            logger.warning("get_order failed for %s: %s", order_id, exc)
            raise

    async def post_heartbeat(self, heartbeat_id: str | None = None) -> str | None:
        """Send a heartbeat to keep live orders active.

        WITHOUT a heartbeat every ≤10s, the CLOB will auto-cancel all open orders.
        Call this every 5s when live trading is active.

        Returns the next heartbeat_id to pass on the following call.

        Raises
        ------
        RuntimeError
            When ``settings.paper_trading`` is True.
        """
        if settings.paper_trading:
            raise RuntimeError("Paper trading mode is ON.")

        sdk = self._get_sdk_client()
        try:
            resp = sdk.post_heartbeat(heartbeat_id)
            return resp.get("heartbeat_id") if isinstance(resp, dict) else None
        except Exception as exc:
            logger.warning("Heartbeat failed: %s", exc)
            return None

    async def cancel_all_orders(self) -> bool:
        """Cancel ALL open orders in a single CLOB call.

        Use this as a safety shutdown: engine restart, risk breach, or emergency stop.
        Equivalent to DELETE /cancel-all on the CLOB API.

        Returns True if the cancellation was accepted by the CLOB.

        Raises
        ------
        RuntimeError
            When ``settings.paper_trading`` is True.
        """
        if settings.paper_trading:
            raise RuntimeError("Paper trading mode is ON.")

        sdk = self._get_sdk_client()
        try:
            resp = sdk.cancel_all()
            cancelled: bool = resp.get("cancelled", False) if isinstance(resp, dict) else bool(resp)
            logger.info("cancel_all_orders: cancelled=%s", cancelled)
            return bool(cancelled)
        except Exception as exc:
            logger.error("cancel_all_orders failed: %s", exc)
            raise

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """List the authenticated user's open orders.

        Raises
        ------
        RuntimeError
            When ``settings.paper_trading`` is True (no auth configured).
        """
        if settings.paper_trading:
            raise RuntimeError(
                "Paper trading mode is ON. Authenticated order queries are disabled."
            )

        sdk = self._get_sdk_client()
        try:
            orders: list[dict[str, Any]] = sdk.get_orders()
            return orders
        except Exception as exc:
            logger.error("Failed to fetch open orders: %s", exc)
            raise


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_market_info(raw: dict[str, Any]) -> MarketInfo:
    """Convert a raw CLOB /markets response item to :class:`MarketInfo`."""
    # Token list normalisation
    tokens: list[dict[str, Any]] = []
    for t in raw.get("tokens", []):
        if isinstance(t, dict):
            tokens.append({"token_id": t.get("token_id", ""), "outcome": t.get("outcome", "")})

    return MarketInfo(
        condition_id=raw.get("condition_id", raw.get("conditionId", "")),
        tokens=tokens,
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        accepting_orders=bool(raw.get("accepting_orders", raw.get("acceptingOrders", True))),
        minimum_order_size=float(raw.get("minimum_order_size", raw.get("minimumOrderSize", 5.0)) or 5.0),
        minimum_tick_size=float(raw.get("minimum_tick_size", raw.get("minimumTickSize", 0.01)) or 0.01),
    )


def _parse_trade(raw: dict[str, Any]) -> Trade:
    """Convert a raw CLOB /trades response item to :class:`Trade`."""
    ts_raw = raw.get("timestamp", raw.get("created_at", ""))
    if isinstance(ts_raw, (int, float)):
        timestamp = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
    elif ts_raw:
        try:
            timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            timestamp = _utcnow()
    else:
        timestamp = _utcnow()

    price = float(raw.get("price", 0))
    size = float(raw.get("size", 0))
    size_usd = float(raw.get("size_usd", price * size))

    return Trade(
        trade_id=str(raw.get("id", raw.get("trade_id", ""))),
        token_id=str(raw.get("asset_id", raw.get("token_id", ""))),
        side=str(raw.get("side", "BUY")).upper(),
        price=price,
        size=size,
        size_usd=size_usd,
        timestamp=timestamp,
        maker_address=str(raw.get("maker_address", "")),
        taker_address=str(raw.get("taker_address", "")),
    )
