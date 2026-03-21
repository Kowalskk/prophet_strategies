"""
Polymarket Gamma API client for market discovery and metadata.

The Gamma API (https://gamma-api.polymarket.com) provides rich market
metadata including question text, description, resolution date, tags,
event groupings, and resolution status.  No API key or authentication is
required for read operations.

Usage
-----
    from prophet.polymarket.gamma_client import GammaClient

    async with GammaClient() as gamma:
        markets = await gamma.search_markets(query="BTC above")
        for m in markets:
            print(m.question, m.token_id_yes)

Key methods
-----------
- :meth:`search_markets` — Free-text search with optional tag/status filters.
- :meth:`get_market` — Fetch a single market by condition ID or slug.
- :meth:`get_events` — Fetch grouped events (e.g. tag="crypto").
- :meth:`get_active_crypto_markets` — Convenience method: returns all active
  BTC/ETH/SOL weekly markets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from prophet.polymarket.models import PolymarketMarket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
_DEFAULT_TIMEOUT = 20.0
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5
_PAGE_SIZE = 100  # Gamma API page size cap


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _gamma_request(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Perform a GET request against the Gamma API with retry logic."""
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.get(endpoint, params=params or {})
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", _RETRY_BACKOFF * attempt))
                logger.warning("Gamma API rate limit; sleeping %.1fs (attempt %d)", wait, attempt)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as exc:
            last_exc = exc
            logger.warning("Gamma network error attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
            await asyncio.sleep(_RETRY_BACKOFF * attempt)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                logger.warning(
                    "Gamma 5xx attempt %d/%d: %s", attempt, _MAX_RETRIES, exc.response.text
                )
                await asyncio.sleep(_RETRY_BACKOFF * attempt)
            else:
                logger.error(
                    "Gamma API %d error: %s %s",
                    exc.response.status_code,
                    endpoint,
                    exc.response.text[:200],
                )
                raise

    exc = RuntimeError(
        f"Gamma API request to {endpoint} failed after {_MAX_RETRIES} attempts"
    )
    if isinstance(last_exc, BaseException):
        raise exc from last_exc
    raise exc


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GammaClient:
    """Async client for the Polymarket Gamma metadata API.

    All methods return :class:`~prophet.polymarket.models.PolymarketMarket`
    instances so that the rest of the engine works with a single, normalised
    market type regardless of which API was queried.

    Parameters
    ----------
    base_url:
        Override for the Gamma API base URL (useful for testing).
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = GAMMA_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "GammaClient":
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
            logger.debug("GammaClient session opened (base=%s)", self._base_url)

    async def close(self) -> None:
        """Close the shared httpx session."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            logger.debug("GammaClient session closed")

    def _ensure_started(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "GammaClient is not started. "
                "Use 'async with GammaClient() as client:' or call await client.start() first."
            )
        return self._http

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    async def search_markets(
        self,
        *,
        query: str | None = None,
        tag: str | None = None,
        active: bool | None = True,
        closed: bool | None = None,
        archived: bool | None = False,
        limit: int = _PAGE_SIZE,
        offset: int = 0,
    ) -> list[PolymarketMarket]:
        """Search Gamma markets with optional filters.

        Parameters
        ----------
        query:
            Free-text search string (e.g. ``"BTC above"``).
        tag:
            Filter by tag slug (e.g. ``"crypto"``).
        active:
            If True, return only active markets.  If False, only inactive.
            If None, no filter is applied.
        closed:
            Filter by closed status.
        archived:
            Filter by archived status (default: exclude archived).
        limit:
            Page size (max 100).
        offset:
            Pagination offset.
        """
        http = self._ensure_started()
        params: dict[str, Any] = {"limit": min(limit, _PAGE_SIZE), "offset": offset}
        if query:
            params["_c"] = query  # Gamma full-text search param
        if tag:
            params["tag"] = tag
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        if archived is not None:
            params["archived"] = str(archived).lower()

        data = await _gamma_request(http, "/markets", params)

        raw_list: list[dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])
        markets = [_parse_gamma_market(r) for r in raw_list if isinstance(r, dict)]
        logger.debug("search_markets(query=%r, tag=%r): %d results", query, tag, len(markets))
        return markets

    async def search_markets_all_pages(
        self,
        *,
        query: str | None = None,
        tag: str | None = None,
        active: bool | None = True,
        closed: bool | None = None,
        archived: bool | None = False,
    ) -> list[PolymarketMarket]:
        """Paginate through all matching markets and return the full list."""
        all_markets: list[PolymarketMarket] = []
        offset = 0

        while True:
            page = await self.search_markets(
                query=query,
                tag=tag,
                active=active,
                closed=closed,
                archived=archived,
                limit=_PAGE_SIZE,
                offset=offset,
            )
            all_markets.extend(page)
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
            if offset > 10_000:  # sanity cap
                logger.warning("Stopped Gamma pagination at offset 10000")
                break

        logger.info(
            "search_markets_all_pages(query=%r, tag=%r): %d total",
            query, tag, len(all_markets),
        )
        return all_markets

    async def get_market(self, condition_id: str) -> PolymarketMarket | None:
        """Fetch a single market by its condition ID.

        Returns None if the market is not found (404).
        """
        http = self._ensure_started()
        try:
            data = await _gamma_request(http, f"/markets/{condition_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.debug("Market not found in Gamma: %s", condition_id)
                return None
            raise

        if isinstance(data, list) and data:
            return _parse_gamma_market(data[0])
        if isinstance(data, dict) and data:
            return _parse_gamma_market(data)
        return None

    async def get_markets_by_condition_ids(
        self, condition_ids: list[str]
    ) -> dict[str, PolymarketMarket]:
        """Batch-fetch markets by condition IDs.

        Returns a dict mapping condition_id → PolymarketMarket.
        Missing markets are omitted from the result.
        """
        results: dict[str, PolymarketMarket] = {}
        # Gamma doesn't support a bulk endpoint, so we fan-out concurrently
        tasks = [self.get_market(cid) for cid in condition_ids]
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        for cid, market in zip(condition_ids, fetched):
            if isinstance(market, PolymarketMarket):
                results[cid] = market
            elif isinstance(market, Exception):
                logger.warning("Failed to fetch market %s: %s", cid, market)
        return results

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def get_events(
        self,
        *,
        tag: str | None = None,
        active: bool | None = True,
        limit: int = _PAGE_SIZE,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch event groups from the Gamma API.

        Events are logical groupings of related markets (e.g. "BTC weekly
        price markets").  Returns the raw event dicts; event parsing is left
        to the scanner since event shape varies more than market shape.

        Parameters
        ----------
        tag:
            Filter events by tag (e.g. ``"crypto"``).
        active:
            If True, return only active events.
        limit:
            Page size.
        offset:
            Pagination offset.
        """
        http = self._ensure_started()
        params: dict[str, Any] = {"limit": min(limit, _PAGE_SIZE), "offset": offset}
        if tag:
            params["tag"] = tag
        if active is not None:
            params["active"] = str(active).lower()

        data = await _gamma_request(http, "/events", params)
        raw_list: list[dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])
        logger.debug("get_events(tag=%r): %d results", tag, len(raw_list))
        return raw_list

    async def get_markets_from_event_slug(
        self, slug: str
    ) -> list[PolymarketMarket]:
        """Fetch all individual markets that belong to an event slug.

        Example slug: ``"bitcoin-above-on-march-19"``

        The Gamma /events endpoint returns events with a nested ``markets``
        list.  Each market inside the event has full token IDs and question
        text.
        """
        http = self._ensure_started()
        data = await _gamma_request(http, "/events", params={"slug": slug})
        raw_list: list[dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])
        if not raw_list:
            logger.debug("No event found for slug: %s", slug)
            return []

        markets: list[PolymarketMarket] = []
        for event in raw_list:
            for raw_market in event.get("markets", []):
                if isinstance(raw_market, dict):
                    markets.append(_parse_gamma_market(raw_market))

        logger.debug("get_markets_from_event_slug(%r): %d markets", slug, len(markets))
        return markets

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def get_active_crypto_markets(
        self,
        cryptos: list[str] | None = None,
    ) -> list[PolymarketMarket]:
        """Return all active crypto price markets from the Gamma API.

        Searches for each crypto symbol in ``cryptos`` (defaults to
        ``["BTC", "ETH", "SOL"]``) and deduplicates by condition_id.

        Parameters
        ----------
        cryptos:
            List of crypto symbols to search for.  Defaults to
            ``["BTC", "ETH", "SOL"]``.
        """
        if cryptos is None:
            cryptos = ["BTC", "ETH", "SOL"]

        seen: set[str] = set()
        all_markets: list[PolymarketMarket] = []

        # First: broad search with the "crypto" tag
        tag_markets = await self.search_markets_all_pages(tag="crypto", active=True, archived=False)
        for m in tag_markets:
            key = m.condition_id or m.id
            if key and key not in seen:
                seen.add(key)
                all_markets.append(m)

        # Then: per-symbol keyword search to catch markets not tagged "crypto"
        for symbol in cryptos:
            keyword_markets = await self.search_markets_all_pages(
                query=symbol, active=True, archived=False
            )
            for m in keyword_markets:
                key = m.condition_id or m.id
                if key and key not in seen:
                    seen.add(key)
                    all_markets.append(m)

        logger.info(
            "get_active_crypto_markets(%s): %d unique markets found",
            cryptos, len(all_markets),
        )
        return all_markets

    async def get_market_resolution(self, condition_id: str) -> dict[str, Any]:
        """Return resolution details for a market.

        Returns a dict with keys:
        - ``resolved`` (bool)
        - ``outcome`` (str | None) — "YES" or "NO"
        - ``resolution_time`` (str | None) — ISO datetime string
        """
        market = await self.get_market(condition_id)
        if market is None:
            return {"resolved": False, "outcome": None, "resolution_time": None}

        return {
            "resolved": market.resolved,
            "outcome": market.outcome,
            "resolution_time": market.end_date_iso,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_gamma_market(raw: dict[str, Any]) -> PolymarketMarket:
    """Normalise a raw Gamma API market record into a :class:`PolymarketMarket`.

    The Gamma API is inconsistent with field names across versions; this
    function handles the most common variations.
    """
    # Market ID — try multiple field names
    market_id = str(
        raw.get("id") or raw.get("market_id") or raw.get("marketId") or ""
    )

    # Condition ID
    condition_id = str(
        raw.get("conditionId") or raw.get("condition_id") or raw.get("id") or ""
    )

    # Question
    question = str(raw.get("question") or raw.get("title") or "")

    # Token IDs — Gamma stores them as a JSON array string OR a list
    clob_token_ids: list[str] = []
    raw_cti = raw.get("clobTokenIds") or raw.get("clob_token_ids") or []
    if isinstance(raw_cti, str):
        import json
        try:
            raw_cti = json.loads(raw_cti)
        except Exception:
            raw_cti = []
    if isinstance(raw_cti, list):
        clob_token_ids = [str(t) for t in raw_cti if t]

    # Tokens list (richer, with outcome labels)
    tokens: list[dict[str, Any]] = []
    outcomes_raw: list[str] = []
    raw_outcomes = raw.get("outcomes") or []
    if isinstance(raw_outcomes, str):
        import json
        try:
            outcomes_raw = json.loads(raw_outcomes)
        except Exception:
            outcomes_raw = []
    elif isinstance(raw_outcomes, list):
        outcomes_raw = raw_outcomes

    for i, outcome in enumerate(outcomes_raw):
        token_id = clob_token_ids[i] if i < len(clob_token_ids) else ""
        tokens.append({"token_id": token_id, "outcome": str(outcome)})

    # Tags
    tags: list[dict[str, Any]] = []
    raw_tags = raw.get("tags") or []
    if isinstance(raw_tags, list):
        tags = [t if isinstance(t, dict) else {"slug": str(t)} for t in raw_tags]

    return PolymarketMarket(
        id=market_id,
        condition_id=condition_id,
        question=question,
        slug=str(raw.get("slug") or ""),
        description=str(raw.get("description") or ""),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        archived=bool(raw.get("archived", False)),
        accepting_orders=bool(raw.get("acceptingOrders", raw.get("accepting_orders", True))),
        accepting_order_timestamp=raw.get("acceptingOrderTimestamp"),
        end_date_iso=raw.get("endDateIso") or raw.get("end_date_iso") or raw.get("endDate"),
        game_start_time=raw.get("gameStartTime"),
        resolution_source=str(raw.get("resolutionSource") or ""),
        resolved=bool(raw.get("resolved", False)),
        outcome=raw.get("outcome") or raw.get("outcomePrices"),
        clob_token_ids=clob_token_ids,
        tokens=tokens,
        last_trade_price=raw.get("lastTradePrice") or raw.get("last_trade_price"),
        best_bid=raw.get("bestBid") or raw.get("best_bid"),
        best_ask=raw.get("bestAsk") or raw.get("best_ask"),
        volume=raw.get("volume") or 0.0,
        liquidity=raw.get("liquidity") or 0.0,
        tags=tags,
        raw=raw,
    )
