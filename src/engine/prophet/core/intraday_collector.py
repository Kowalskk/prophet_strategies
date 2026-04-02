"""
Intraday Crypto Market Collector — scheduled job (every 30 min).

Fetches short-duration binary crypto markets from Polymarket:
  - 5min/15min/4h: {crypto}-updown-{tf}-{timestamp} (up or down)
  - Hourly: {crypto}-above-on-{date}-{hour} (threshold markets, 10 per event)
  - Daily: {crypto}-up-or-down-{date} (up or down)

Cryptos: BTC, ETH, SOL, HYPE, DOGE, BNB, XRP
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Slug prefixes we care about
_CRYPTO_SLUGS = ("btc", "eth", "sol", "hype", "bitcoin", "ethereum", "solana",
                 "doge", "dogecoin", "bnb")

# Patterns that identify intraday crypto events
_UPDOWN_RE = re.compile(
    r"^(btc|eth|sol|hype|doge|bnb|bitcoin|ethereum|solana|dogecoin|hyperliquid)"
    r"-(updown|up-or-down)-"
)
_ABOVE_RE = re.compile(
    r"^(bitcoin|ethereum|solana|hype|dogecoin|bnb)"
    r"-above-on-"
)

_CRYPTO_MAP = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "hype": "HYPE", "hyperliquid": "HYPE",
    "doge": "DOGE", "dogecoin": "DOGE",
    "bnb": "BNB",
}


def _classify_event(slug: str, title: str) -> dict | None:
    """Return {crypto, timeframe} if this is an intraday crypto event, else None."""
    # updown-5m / updown-15m / updown-4h
    m = re.match(r"(\w+)-updown-(\w+)-(\d+)", slug)
    if m:
        crypto_raw, tf, _ = m.groups()
        crypto = _CRYPTO_MAP.get(crypto_raw)
        if crypto and tf in ("5m", "15m", "4h"):
            return {"crypto": crypto, "timeframe": tf}

    # up-or-down (daily / hourly single market)
    m = _UPDOWN_RE.match(slug)
    if m:
        crypto = _CRYPTO_MAP.get(m.group(1))
        if crypto:
            # Has specific hour = hourly, else daily
            has_hour = bool(re.search(r"\d+(am|pm)-et", slug))
            return {"crypto": crypto, "timeframe": "1h" if has_hour else "daily"}

    # above-on (hourly threshold markets, ~10 per event)
    m = _ABOVE_RE.match(slug)
    if m:
        crypto = _CRYPTO_MAP.get(m.group(1))
        if crypto:
            has_hour = bool(re.search(r"\d+(am|pm)-et", slug))
            return {"crypto": crypto, "timeframe": "1h" if has_hour else "weekly"}

    return None


def _detect_threshold(question: str) -> float | None:
    m = re.search(r"\$([0-9,]+(?:\.\d+)?)", question)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _detect_direction(question: str) -> str:
    q = question.lower()
    if "above" in q or "up" in q:
        return "above"
    if "below" in q or "down" in q:
        return "below"
    return "unknown"


class IntradayCollector:
    """Collects intraday crypto market data from Polymarket APIs."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def collect(self) -> str:
        """Main entry — called by scheduler every 30 min."""
        from prophet.db.database import get_session
        from prophet.db.models import IntradayMarket, IntradayTrade
        from sqlalchemy import select

        http = await self._get_client()
        new_markets = 0
        new_trades = 0

        # Scan both open and closed events
        for closed_flag in ("false", "true"):
            offset = 0
            empty_streak = 0

            while empty_streak < 5 and offset < 100000:
                events = await self._fetch_events(http, offset, closed_flag)
                if not events:
                    empty_streak += 1
                    offset += 100
                    continue
                empty_streak = 0

                for ev in events:
                    slug = ev.get("slug", "")
                    title = ev.get("title", "")
                    info = _classify_event(slug, title)
                    if not info:
                        continue

                    # Process each market in the event
                    ev_markets = ev.get("markets", [])
                    for m in ev_markets:
                        cid = m.get("conditionId", m.get("condition_id", ""))
                        if not cid:
                            continue

                        q = m.get("question", "")

                        async with get_session() as db:
                            exists = (await db.execute(
                                select(IntradayMarket.id).where(
                                    IntradayMarket.condition_id == cid
                                ).limit(1)
                            )).scalar()

                            if exists:
                                continue

                            db.add(IntradayMarket(
                                condition_id=cid,
                                question=q,
                                slug=slug,
                                crypto=info["crypto"],
                                threshold=_detect_threshold(q),
                                direction=_detect_direction(q),
                                timeframe=info["timeframe"],
                                resolution_date=m.get("endDateIso", m.get("end_date_iso", "")),
                                outcome=str(m.get("outcomes", m.get("outcome", "")))[:200],
                                end_date=m.get("endDateIso", m.get("end_date_iso", "")),
                                volume=float(m.get("volume", 0) or 0),
                                liquidity=float(m.get("liquidity", 0) or 0),
                                created_at=m.get("createdAt", m.get("created_at", "")),
                            ))
                            await db.commit()
                            new_markets += 1

                            # Fetch trades
                            trades = await self._fetch_trades(http, cid)
                            if trades:
                                n = await self._save_trades(db, cid, trades)
                                new_trades += n

                offset += 100

        msg = f"IntradayCollector: {new_markets} new markets, {new_trades} new trades"
        if new_markets > 0:
            logger.info(msg)
        else:
            logger.debug(msg)
        return msg

    async def _fetch_events(
        self, http: httpx.AsyncClient, offset: int, closed: str
    ) -> list[dict]:
        try:
            resp = await http.get(
                f"{GAMMA_BASE}/events",
                params={
                    "limit": 100,
                    "offset": offset,
                    "order": "startDate",
                    "ascending": "false",
                    "closed": closed,
                },
            )
            if resp.status_code != 200:
                return []
            return resp.json()
        except Exception as exc:
            logger.debug("Gamma events fetch error at offset %d: %s", offset, exc)
            return []

    async def _fetch_trades(self, http: httpx.AsyncClient, condition_id: str) -> list[dict]:
        all_trades: list[dict] = []
        next_cursor = ""

        for _ in range(20):
            params: dict[str, Any] = {"market": condition_id, "limit": 500}
            if next_cursor:
                params["next_cursor"] = next_cursor

            try:
                resp = await http.get(f"{CLOB_BASE}/trades", params=params)
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception:
                break

            trades = data.get("data", [])
            if not trades:
                break

            all_trades.extend(trades)
            next_cursor = data.get("next_cursor", "")
            if not next_cursor or next_cursor == "LTE=":
                break

        return all_trades

    async def _save_trades(
        self, db: Any, condition_id: str, trades: list[dict]
    ) -> int:
        from prophet.db.models import IntradayTrade
        from sqlalchemy import select

        count = 0
        for t in trades:
            tid = t.get("id", "")
            try:
                exists = (await db.execute(
                    select(IntradayTrade.id).where(
                        IntradayTrade.condition_id == condition_id,
                        IntradayTrade.trade_id == tid,
                    ).limit(1)
                )).scalar()
                if exists:
                    continue
                db.add(IntradayTrade(
                    condition_id=condition_id,
                    trade_id=tid,
                    price=float(t.get("price", 0) or 0),
                    size=float(t.get("size", 0) or 0),
                    side=t.get("side", ""),
                    timestamp=t.get("created_at", t.get("timestamp", "")),
                ))
                count += 1
            except Exception:
                pass
        await db.commit()
        return count

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()
