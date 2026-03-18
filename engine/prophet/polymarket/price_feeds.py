"""
Spot price feeds for BTC, ETH, and SOL.

:class:`PriceFeedService` fetches current prices from external APIs and
persists them to PostgreSQL (``price_snapshots`` table) and Redis.

Data sources (in priority order)
---------------------------------
1. **Binance** — ``GET https://api.binance.com/api/v3/ticker/price``
   No API key required.  Symbol format: ``BTCUSDT``, ``ETHUSDT``, ``SOLUSDT``.
2. **CoinGecko** — ``GET https://api.coingecko.com/api/v3/simple/price``
   No API key required for the free tier (rate-limited to ~30 req/min).
   Used as automatic fallback if Binance is unavailable.

Redis caching
-------------
Latest prices are cached in Redis with key ``price:{symbol}`` and 5-minute TTL.
Format: ``{"price_usd": 65000.0, "source": "binance", "timestamp": "..."}``

Usage
-----
    service = PriceFeedService(db_session=session, redis_client=redis)
    await service.start()
    prices = await service.fetch_all()
    # {"BTC": PriceData(...), "ETH": PriceData(...), "SOL": PriceData(...)}
    await service.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from prophet.config import settings
from prophet.polymarket.models import PriceData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

# Mapping: Prophet symbol → Binance trading pair
_BINANCE_SYMBOLS: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

# Mapping: Prophet symbol → CoinGecko coin ID
_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}

_DEFAULT_TIMEOUT = 10.0
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
) -> Any | None:
    """GET with retries; returns parsed JSON or None on failure."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.get(url, params=params or {})
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", _RETRY_BACKOFF * attempt))
                logger.warning("Price feed rate limited (attempt %d): sleeping %.1fs", attempt, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as exc:
            logger.warning("Price feed network error attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
            await asyncio.sleep(_RETRY_BACKOFF * attempt)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    "Price feed 5xx attempt %d/%d: %s", attempt, _MAX_RETRIES, exc.response.text
                )
                await asyncio.sleep(_RETRY_BACKOFF * attempt)
            else:
                logger.error("Price feed %d: %s", exc.response.status_code, url)
                return None
    return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PriceFeedService:
    """Fetches and persists BTC/ETH/SOL spot prices.

    Parameters
    ----------
    db_session:
        SQLAlchemy ``AsyncSession``.  If None, prices are not persisted to DB.
    redis_client:
        Async Redis client.  If None, Redis caching is skipped.
    symbols:
        List of crypto symbols to track.  Defaults to ``settings.target_cryptos``.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        db_session: Any | None = None,
        redis_client: Any | None = None,
        symbols: list[str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._db = db_session
        self._redis = redis_client
        self._symbols: list[str] = [s.upper() for s in (symbols or settings.target_cryptos)]
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

        # In-memory cache: symbol → PriceData (set after each successful fetch)
        self._latest: dict[str, PriceData] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the shared HTTP session."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
            logger.debug("PriceFeedService HTTP session opened")

    async def close(self) -> None:
        """Close the shared HTTP session."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            logger.debug("PriceFeedService HTTP session closed")

    async def __aenter__(self) -> "PriceFeedService":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_all(self) -> dict[str, PriceData]:
        """Fetch current prices for all tracked symbols.

        Tries Binance first; falls back to CoinGecko for any symbols that
        fail.  Persists to DB and Redis after each successful fetch batch.

        Returns
        -------
        dict
            ``{symbol: PriceData}`` for all symbols that were successfully
            fetched.  Missing symbols are omitted.
        """
        if self._http is None:
            await self.start()

        results: dict[str, PriceData] = {}

        # --- Primary: Binance ---
        binance_results = await self._fetch_from_binance()
        results.update(binance_results)

        # --- Fallback: CoinGecko for any missing symbols ---
        missing = [s for s in self._symbols if s not in results]
        if missing:
            logger.info(
                "Binance missing %d symbol(s) %s — trying CoinGecko", len(missing), missing
            )
            coingecko_results = await self._fetch_from_coingecko(missing)
            results.update(coingecko_results)

        # Report final coverage
        still_missing = [s for s in self._symbols if s not in results]
        if still_missing:
            logger.warning("No price data for: %s", still_missing)

        if results:
            self._latest.update(results)
            await self._persist(list(results.values()))
            await self._cache(list(results.values()))

        return results

    async def fetch_symbol(self, symbol: str) -> PriceData | None:
        """Fetch price for a single symbol.

        Returns None if both Binance and CoinGecko fail.
        """
        all_prices = await self.fetch_all()
        return all_prices.get(symbol.upper())

    def get_latest(self, symbol: str) -> PriceData | None:
        """Return the most recently fetched price (from in-memory cache)."""
        return self._latest.get(symbol.upper())

    async def get_cached(self, symbol: str) -> PriceData | None:
        """Read the latest price from Redis (may be stale up to TTL).

        Returns None if not cached or Redis is unavailable.
        """
        if self._redis is None:
            return self.get_latest(symbol)

        key = f"price:{symbol.upper()}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            payload = json.loads(raw)
            return PriceData(
                symbol=symbol.upper(),
                price_usd=payload["price_usd"],
                source=payload["source"],
                timestamp=datetime.fromisoformat(payload["timestamp"]),
            )
        except Exception as exc:
            logger.warning("Redis price cache read failed for %s: %s", symbol, exc)
            return None

    # ------------------------------------------------------------------
    # Binance
    # ------------------------------------------------------------------

    async def _fetch_from_binance(self) -> dict[str, PriceData]:
        """Fetch all tracked symbols from Binance in a single request.

        Binance supports fetching multiple tickers by passing a JSON array
        of symbols: ``?symbols=["BTCUSDT","ETHUSDT","SOLUSDT"]``
        """
        assert self._http is not None

        binance_symbols = [
            _BINANCE_SYMBOLS[s] for s in self._symbols if s in _BINANCE_SYMBOLS
        ]
        if not binance_symbols:
            return {}

        params: dict[str, Any] = {"symbols": json.dumps(binance_symbols)}
        ts = _utcnow()

        data = await _safe_get(self._http, BINANCE_TICKER_URL, params)
        if data is None:
            logger.warning("Binance price fetch returned no data")
            return {}

        # Response is either a list (multi-symbol) or a single dict
        if isinstance(data, dict):
            data = [data]

        # Build reverse mapping: BTCUSDT → BTC
        binance_to_symbol = {v: k for k, v in _BINANCE_SYMBOLS.items()}

        results: dict[str, PriceData] = {}
        for item in data:
            pair = item.get("symbol", "")
            raw_price = item.get("price")
            if raw_price is None:
                continue
            symbol = binance_to_symbol.get(pair)
            if symbol is None:
                continue
            try:
                price = float(raw_price)
                results[symbol] = PriceData(
                    symbol=symbol,
                    price_usd=price,
                    source="binance",
                    timestamp=ts,
                )
                logger.debug("Binance: %s = $%.2f", symbol, price)
            except (ValueError, TypeError) as exc:
                logger.warning("Binance bad price for %s: %s", pair, exc)

        return results

    # ------------------------------------------------------------------
    # CoinGecko
    # ------------------------------------------------------------------

    async def _fetch_from_coingecko(
        self, symbols: list[str]
    ) -> dict[str, PriceData]:
        """Fetch prices from CoinGecko for the given symbols.

        Uses the ``/simple/price`` endpoint which requires no API key.
        """
        assert self._http is not None

        coin_ids = [_COINGECKO_IDS[s] for s in symbols if s in _COINGECKO_IDS]
        if not coin_ids:
            return {}

        params = {
            "ids": ",".join(coin_ids),
            "vs_currencies": "usd",
        }
        ts = _utcnow()

        data = await _safe_get(self._http, COINGECKO_PRICE_URL, params)
        if data is None:
            logger.warning("CoinGecko price fetch returned no data")
            return {}

        # Build reverse mapping: "bitcoin" → "BTC"
        cg_to_symbol = {v: k for k, v in _COINGECKO_IDS.items()}

        results: dict[str, PriceData] = {}
        for coin_id, prices in data.items():
            symbol = cg_to_symbol.get(coin_id)
            if symbol is None:
                continue
            raw_price = prices.get("usd")
            if raw_price is None:
                continue
            try:
                price = float(raw_price)
                results[symbol] = PriceData(
                    symbol=symbol,
                    price_usd=price,
                    source="coingecko",
                    timestamp=ts,
                )
                logger.debug("CoinGecko: %s = $%.2f", symbol, price)
            except (ValueError, TypeError) as exc:
                logger.warning("CoinGecko bad price for %s: %s", coin_id, exc)

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist(self, prices: list[PriceData]) -> None:
        """Insert ``price_snapshots`` rows for each :class:`PriceData`."""
        if self._db is None or not prices:
            return

        try:
            from prophet.db.models import PriceSnapshot

            for p in prices:
                snapshot = PriceSnapshot(
                    crypto=p.symbol,
                    price_usd=p.price_usd,
                    source=p.source,
                    timestamp=p.timestamp,
                )
                self._db.add(snapshot)

            await self._db.flush()
            logger.debug("Persisted %d price snapshot(s) to DB", len(prices))
        except Exception as exc:
            logger.error("Failed to persist price snapshots: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Redis caching
    # ------------------------------------------------------------------

    async def _cache(self, prices: list[PriceData]) -> None:
        """Write each price to Redis with a 5-minute TTL."""
        if self._redis is None or not prices:
            return

        for p in prices:
            key = f"price:{p.symbol}"
            payload = {
                "price_usd": p.price_usd,
                "source": p.source,
                "timestamp": p.timestamp.isoformat(),
            }
            try:
                await self._redis.setex(key, 300, json.dumps(payload))
                logger.debug("Cached price in Redis: %s = $%.2f", p.symbol, p.price_usd)
            except Exception as exc:
                logger.warning("Redis price cache write failed for %s: %s", p.symbol, exc)
