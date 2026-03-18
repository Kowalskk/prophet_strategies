"""
Market Scanner — discovers and tracks Polymarket weekly crypto price markets.

:class:`MarketScanner` queries the Gamma API for active BTC/ETH/SOL price
markets, parses the question text to extract structured data (crypto, threshold,
direction, resolution_date), and persists new markets to PostgreSQL.

Schedule
--------
- Full scan: Every Monday 00:00 UTC — new weekly markets appear on Polymarket
  at the start of each week.
- Quick scan: Every 15 minutes — catch stragglers and update resolution status.

Parsing
-------
Regex patterns are adapted from the existing backtest system's
``data/market_resolver.py``.  Only markets matching ALL of:
  - crypto in [BTC, ETH, SOL]
  - direction in [ABOVE, BELOW]
  - no reject patterns (hit / first / again / ever / in 202X / day-of-week)

are stored and tracked.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.config import settings
from prophet.polymarket.gamma_client import GammaClient
from prophet.polymarket.models import PolymarketMarket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns (ported from data/market_resolver.py)
# ---------------------------------------------------------------------------

# Crypto detection
_CRYPTO_PATTERNS: dict[str, re.Pattern[str]] = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE),
    "ETH": re.compile(r"\b(ethereum|eth)\b", re.IGNORECASE),
    "SOL": re.compile(r"\b(solana|sol)\b", re.IGNORECASE),
}

# Price threshold: $74,000 or $74000 or $5,000.50
_THRESHOLD_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")

# Direction
_ABOVE_PATTERN = re.compile(r"\b(above|over)\b", re.IGNORECASE)
_BELOW_PATTERN = re.compile(r"\b(below|under)\b", re.IGNORECASE)

# Hard rejects — markets we never want even if they pass other filters
_REJECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bhit\b", re.IGNORECASE),
    re.compile(r"\bor\b.{0,20}\bfirst\b", re.IGNORECASE),
    re.compile(r"\bin 202\d\b", re.IGNORECASE),
    re.compile(r"\bagain\b", re.IGNORECASE),
    re.compile(r"\bever\b", re.IGNORECASE),
    re.compile(r"\btoday\b", re.IGNORECASE),
    re.compile(
        r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b",
        re.IGNORECASE,
    ),
]

# Month abbreviation map
_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def _is_rejected(question: str) -> bool:
    """Return True if the question matches any hard-reject pattern."""
    return any(p.search(question) for p in _REJECT_PATTERNS)


def _extract_crypto(question: str) -> str | None:
    """Return the first matching crypto symbol or None."""
    for symbol, pattern in _CRYPTO_PATTERNS.items():
        if pattern.search(question):
            return symbol
    return None


def _extract_threshold(question: str) -> float | None:
    """Return the largest dollar amount found in the question, or None."""
    matches = _THRESHOLD_PATTERN.findall(question)
    if not matches:
        return None
    values: list[float] = []
    for m in matches:
        try:
            values.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return max(values) if values else None


def _extract_direction(question: str) -> str | None:
    """Return 'ABOVE', 'BELOW', or None."""
    if _BELOW_PATTERN.search(question):
        return "BELOW"
    if _ABOVE_PATTERN.search(question):
        return "ABOVE"
    return None


def _extract_date(question: str) -> date | None:
    """Try multiple patterns to extract a resolution date from the question."""
    today = date.today()

    # Pattern 1: "on/by MonthName Day, Year"
    m = re.search(
        r"\b(?:on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b",
        question,
        re.IGNORECASE,
    )
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                pass

    # Pattern 2: "on/by MonthName Day" (no year — infer)
    m = re.search(
        r"\b(?:on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        question,
        re.IGNORECASE,
    )
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        if month:
            year = today.year
            try:
                d = date(year, month, int(m.group(2)))
                if d < today:
                    d = date(year + 1, month, int(m.group(2)))
                return d
            except ValueError:
                pass

    # Pattern 3: ISO "on/by 2025-03-03"
    m = re.search(r"\b(?:on|by)\s+(\d{4}-\d{2}-\d{2})\b", question, re.IGNORECASE)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass

    # Pattern 4: "Mar 3" loose
    m = re.search(
        r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", question, re.IGNORECASE
    )
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        if month:
            year = today.year
            try:
                d = date(year, month, int(m.group(2)))
                if d < today:
                    d = date(year + 1, month, int(m.group(2)))
                return d
            except ValueError:
                pass

    return None


def parse_market_question(
    question: str,
) -> dict[str, Any]:
    """Parse a Polymarket question string into structured fields.

    Returns a dict with keys:
    - ``crypto``         — str | None
    - ``threshold``      — float | None
    - ``direction``      — 'ABOVE' | 'BELOW' | None
    - ``resolution_date``— date | None
    - ``parseable``      — bool (True if all required fields were extracted)
    """
    result: dict[str, Any] = {
        "crypto": None,
        "threshold": None,
        "direction": None,
        "resolution_date": None,
        "parseable": False,
    }

    if _is_rejected(question):
        logger.debug("Question rejected by filter: %r", question)
        return result

    crypto = _extract_crypto(question)
    threshold = _extract_threshold(question)
    direction = _extract_direction(question)
    resolution_date = _extract_date(question)

    result["crypto"] = crypto
    result["threshold"] = threshold
    result["direction"] = direction
    result["resolution_date"] = resolution_date
    result["parseable"] = bool(crypto and direction)

    if not result["parseable"]:
        logger.debug("Unparseable question: %r", question)

    return result


# ---------------------------------------------------------------------------
# MarketScanner
# ---------------------------------------------------------------------------


class MarketScanner:
    """Discovers and tracks Polymarket weekly crypto price markets.

    Parameters
    ----------
    gamma_client:
        An already-started :class:`~prophet.polymarket.gamma_client.GammaClient`.
    db_session:
        SQLAlchemy async session for persisting market data.
    """

    def __init__(
        self,
        gamma_client: GammaClient,
        db_session: AsyncSession,
    ) -> None:
        self._gamma = gamma_client
        self._db = db_session

    # ------------------------------------------------------------------
    # Public scan entry points
    # ------------------------------------------------------------------

    async def full_scan(self) -> dict[str, int]:
        """Full market scan — intended for Monday 00:00 UTC.

        Fetches ALL active crypto markets from Gamma, upserts new ones,
        and updates resolution status of existing markets.

        Returns
        -------
        dict
            ``{"new": int, "updated": int, "skipped": int}``
        """
        logger.info("MarketScanner: starting FULL scan")
        raw_markets = await self._gamma.get_active_crypto_markets(
            cryptos=settings.target_cryptos
        )
        stats = await self._process_markets(raw_markets)
        await self._update_resolved_markets()
        try:
            await self._db.commit()
        except Exception as exc:
            logger.error("MarketScanner: full_scan commit failed: %s", exc)
            await self._db.rollback()
        logger.info(
            "MarketScanner: full scan done — new=%d updated=%d skipped=%d",
            stats["new"], stats["updated"], stats["skipped"],
        )
        return stats

    async def quick_scan(self) -> dict[str, int]:
        """Quick scan — runs every 15 minutes.

        Searches for new markets using keyword queries per crypto and
        updates resolution status of active markets.

        Returns
        -------
        dict
            ``{"new": int, "updated": int, "skipped": int}``
        """
        logger.info("MarketScanner: starting QUICK scan")
        raw_markets: list[PolymarketMarket] = []

        # Primary method: fetch daily markets via event slugs for the next 7 days.
        # Slugs follow the pattern "{crypto}-above-on-{month}-{day}".
        # This is more reliable than keyword search which returns stale 2020 markets.
        from datetime import timedelta
        _CRYPTO_SLUG_PREFIX: dict[str, str] = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
        }
        _MONTH_SLUG: dict[int, str] = {
            1: "january", 2: "february", 3: "march", 4: "april",
            5: "may", 6: "june", 7: "july", 8: "august",
            9: "september", 10: "october", 11: "november", 12: "december",
        }
        today = date.today()
        for delta in range(0, 8):
            target_date = today + timedelta(days=delta)
            month_name = _MONTH_SLUG[target_date.month]
            for crypto in settings.target_cryptos:
                prefix = _CRYPTO_SLUG_PREFIX.get(crypto, crypto.lower())
                slug = f"{prefix}-above-on-{month_name}-{target_date.day}"
                try:
                    results: list[PolymarketMarket] = await self._gamma.get_markets_from_event_slug(slug)
                    if results:
                        logger.debug("Event slug %s: %d markets", slug, len(results))
                    raw_markets.extend(results)
                except Exception as exc:
                    logger.debug("Slug scan skip %s: %s", slug, exc)

        stats = await self._process_markets(raw_markets)
        await self._update_resolved_markets()
        try:
            await self._db.commit()
        except Exception as exc:
            logger.error("MarketScanner: quick_scan commit failed: %s", exc)
            await self._db.rollback()
        logger.info(
            "MarketScanner: quick scan done — new=%d updated=%d skipped=%d",
            stats["new"], stats["updated"], stats["skipped"],
        )
        return stats

    # ------------------------------------------------------------------
    # Internal processing
    # ------------------------------------------------------------------

    async def _process_markets(
        self, raw_markets: list[PolymarketMarket]
    ) -> dict[str, int]:
        """Upsert a list of raw Gamma markets into the DB.

        Returns counts: new, updated, skipped.
        """
        from prophet.db.models import Market

        stats = {"new": 0, "updated": 0, "skipped": 0}
        seen_condition_ids: set[str] = set()

        for pm in raw_markets:
            condition_id = pm.condition_id or pm.id
            if not condition_id or condition_id in seen_condition_ids:
                continue
            seen_condition_ids.add(condition_id)

            # Skip markets without token IDs (can't trade without them)
            token_yes = pm.token_id_yes
            token_no = pm.token_id_no
            if not token_yes or not token_no:
                logger.debug(
                    "Skipping market %s — missing token IDs", condition_id
                )
                stats["skipped"] += 1
                continue

            # Parse question
            parsed = parse_market_question(pm.question)
            if not parsed["parseable"]:
                stats["skipped"] += 1
                continue

            # Filter to target cryptos
            if parsed["crypto"] not in settings.target_cryptos:
                stats["skipped"] += 1
                continue

            # Check if already in DB
            existing = await self._get_market_by_condition_id(condition_id)

            if existing is None:
                # Insert new market
                market = Market(
                    condition_id=condition_id,
                    question=pm.question,
                    crypto=parsed["crypto"],
                    threshold=parsed["threshold"],
                    direction=parsed["direction"],
                    resolution_date=parsed["resolution_date"],
                    token_id_yes=token_yes,
                    token_id_no=token_no,
                    status="active",
                    resolved_outcome=None,
                    resolution_time=None,
                )
                self._db.add(market)
                stats["new"] += 1
                logger.info(
                    "New market: %s | %s | %s %s",
                    condition_id[:12],
                    parsed["crypto"],
                    parsed["direction"],
                    parsed["threshold"],
                )
            else:
                # Update resolution data if market is now resolved
                changed = False
                if pm.resolved and pm.outcome and existing.status == "active":
                    existing.status = "resolved"
                    existing.resolved_outcome = str(pm.outcome).upper()
                    if pm.end_date_iso:
                        try:
                            existing.resolution_time = datetime.fromisoformat(
                                pm.end_date_iso.replace("Z", "+00:00")
                            )
                        except ValueError:
                            existing.resolution_time = datetime.now(timezone.utc)
                    else:
                        existing.resolution_time = datetime.now(timezone.utc)
                    changed = True
                    logger.info(
                        "Market resolved: %s → %s",
                        condition_id[:12],
                        existing.resolved_outcome,
                    )

                if changed:
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1

        try:
            await self._db.flush()
        except Exception as exc:
            logger.error("DB flush failed during market processing: %s", exc)
            raise

        return stats

    async def _update_resolved_markets(self) -> None:
        """Check all active DB markets against Gamma for resolution updates."""
        from prophet.db.models import Market

        stmt = select(Market).where(Market.status == "active")
        result = await self._db.execute(stmt)
        active_markets = result.scalars().all()

        if not active_markets:
            return

        logger.debug(
            "Checking resolution status for %d active markets", len(active_markets)
        )

        for market in active_markets:
            try:
                resolution = await self._gamma.get_market_resolution(
                    market.condition_id
                )
                if resolution["resolved"] and resolution["outcome"]:
                    market.status = "resolved"
                    market.resolved_outcome = str(resolution["outcome"]).upper()
                    if resolution["resolution_time"]:
                        try:
                            market.resolution_time = datetime.fromisoformat(
                                str(resolution["resolution_time"]).replace("Z", "+00:00")
                            )
                        except ValueError:
                            market.resolution_time = datetime.now(timezone.utc)
                    else:
                        market.resolution_time = datetime.now(timezone.utc)

                    logger.info(
                        "Resolution update: market_id=%d %s → %s",
                        market.id,
                        market.condition_id[:12],
                        market.resolved_outcome,
                    )
            except Exception as exc:
                logger.warning(
                    "Could not check resolution for market %s: %s",
                    market.condition_id[:12],
                    exc,
                )

        try:
            await self._db.flush()
        except Exception as exc:
            logger.error("DB flush failed during resolution update: %s", exc)
            raise

    async def _get_market_by_condition_id(
        self, condition_id: str
    ) -> Any | None:
        """Return the DB Market row matching condition_id, or None."""
        from prophet.db.models import Market

        stmt = select(Market).where(Market.condition_id == condition_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()
