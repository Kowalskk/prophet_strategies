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
_DEFAULT_TIMEOUT = 15.0  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds — multiplied by attempt number


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
        """Open the shared httpx session."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
            logger.debug("PolymarketClient HTTP session opened (base=%s)", self._base_url)

    async def close(self) -> None:
        """Close the shared httpx session."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            logger.debug("PolymarketClient HTTP session closed")

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
        before: str | None = None,
        after: str | None = None,
    ) -> list[Trade]:
        """Fetch recent trades for a token.

        Parameters
        ----------
        token_id:
            CLOB token ID to query.
        limit:
            Maximum number of trades to return.
        before:
            Return trades with timestamp < this ISO string.
        after:
            Return trades with timestamp > this ISO string.
        """
        http = self._ensure_started()
        params: dict[str, Any] = {"token_id": token_id, "limit": limit}
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        data = await _request_with_retry(http, "GET", "/trades", params=params)
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
