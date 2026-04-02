"""
Market Scanner — discovers and tracks Polymarket markets across all categories.

:class:`MarketScanner` queries the Gamma API for active markets across multiple
categories (crypto, sports, politics, entertainment, etc.), parses market data,
and persists new markets to PostgreSQL.

Category-aware scanning
-----------------------
Based on research (Becker 2026), different categories have different edge sizes:
- Sports: 2.23% maker-taker gap (72% of volume) — HIGHEST PRIORITY
- Entertainment: 4.79% gap — HIGH edge but low volume
- Politics: 1.02% gap — MODERATE
- Finance/Crypto: 0.17% gap — LOW (but familiar territory)

Schedule
--------
- Full scan: Every Monday 00:00 UTC — discovers new markets across all categories
- Quick scan: Every 15 minutes — catch new markets + update resolution status

Strategy assignment
-------------------
Markets are auto-assigned strategies based on their category:
- crypto: SRB variants + volatility_spread + liquidity_sniper
- sports: SRB + DCA + reversal (high-edge, fast resolution)
- politics: SRB + pre_window (longer resolution, FIFO advantage)
- entertainment: SRB + ladder_mm (high edge, thin books)
- other: SRB only (conservative default)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.config import settings
from prophet.polymarket.gamma_client import GammaClient
from prophet.polymarket.models import PolymarketMarket

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category tags to scan on Gamma API
# ---------------------------------------------------------------------------

SCAN_CATEGORIES: dict[str, list[str]] = {
    "crypto": ["crypto"],
    "sports": ["sports", "nba", "nfl", "mlb", "soccer", "mma", "ufc", "tennis"],
    "politics": ["politics", "elections", "congress"],
    "entertainment": ["entertainment", "pop culture", "media"],
    "science": ["science", "climate", "weather", "temperature", "recurring"],
    "economics": ["economics", "fed", "macro"],
}

# Strategy assignments per category (based on Becker 2026 research)
CATEGORY_STRATEGIES: dict[str, list[str]] = {
    "crypto": [
        # SRB — cheap tier
        "srb_cheap_res", "srb_cheap_x1p3", "srb_cheap_x1p5",
        "srb_cheap_x3", "srb_cheap_x4", "srb_cheap_x5",
        "srb_cheap_x6", "srb_cheap_x7", "srb_cheap_x8", "srb_cheap_x9", "srb_cheap_x10",
        "srb_cheap_x12", "srb_cheap_x15", "srb_cheap_x20", "srb_cheap_x25", "srb_cheap_x30",
        "srb_cheap_te",
        # SRB — mid tier
        "srb_mid_res", "srb_mid_x1p3", "srb_mid_x1p5",
        "srb_mid_x3", "srb_mid_x4", "srb_mid_x5",
        "srb_mid_x6", "srb_mid_x7", "srb_mid_x8", "srb_mid_x9", "srb_mid_x10",
        "srb_mid_x12", "srb_mid_x15", "srb_mid_x18",
        "srb_mid_te",
        # SRB — high tier
        "srb_high_res", "srb_high_x1p3", "srb_high_x1p5",
        "srb_high_x2", "srb_high_x3", "srb_high_x4", "srb_high_x5", "srb_high_x10", "srb_high_te",
        # SRB — fat tier
        "srb_fat_res", "srb_fat_x2", "srb_fat_x3", "srb_fat_x30", "srb_fat_x50",
        # Volatility Spread
        "volatility_spread", "vs_x3", "vs_x4", "vs_x5",
        # Straddle
        "straddle_x3", "straddle_x5", "straddle_te",
        # Contra-SRB (last-minute lottery) — all windows × all percentiles
        "csrb_48h_2c_p50", "csrb_48h_2c_p60", "csrb_48h_2c_p70", "csrb_48h_2c_p75",
        "csrb_48h_2c_p80", "csrb_48h_2c_p90", "csrb_48h_2c_p95", "csrb_48h_2c_p99",
        "csrb_24h_2c_p50", "csrb_24h_2c_p60", "csrb_24h_2c_p70", "csrb_24h_2c_p75",
        "csrb_24h_2c_p80", "csrb_24h_2c_p90", "csrb_24h_2c_p95", "csrb_24h_2c_p99",
        "csrb_12h_2c_p50", "csrb_12h_2c_p60", "csrb_12h_2c_p70", "csrb_12h_2c_p75",
        "csrb_12h_2c_p80", "csrb_12h_2c_p90", "csrb_12h_2c_p95", "csrb_12h_2c_p99",
        # Time-windowed SRB (4h/6h/12h/24h/48h before resolution)
        "srb_cheap_res_4h", "srb_cheap_res_6h", "srb_cheap_res_12h", "srb_cheap_res_24h", "srb_cheap_res_48h",
        "srb_cheap_x5_4h", "srb_cheap_x5_6h", "srb_cheap_x5_12h", "srb_cheap_x5_24h", "srb_cheap_x5_48h",
        "srb_cheap_x10_4h", "srb_cheap_x10_6h", "srb_cheap_x10_12h", "srb_cheap_x10_24h", "srb_cheap_x10_48h",
        "srb_mid_res_4h", "srb_mid_res_6h", "srb_mid_res_24h", "srb_mid_res_48h",
        "srb_mid_x5_4h", "srb_mid_x5_6h", "srb_mid_x5_12h", "srb_mid_x5_24h", "srb_mid_x5_48h",
        "srb_mid_x10_4h", "srb_mid_x10_6h", "srb_mid_x10_12h", "srb_mid_x10_24h", "srb_mid_x10_48h",
    ],
    "sports": [
        "srb_generic_res", "srb_generic_x5", "srb_generic_x10",
        "volatility_spread",
        "dca", "dca_sports",
        "reversal", "reversal_aggressive",
    ],
    "politics": [
        "srb_generic_res", "srb_generic_x5",
        "volatility_spread",
        "pre_window", "pre_window_early", "pre_window_late",
        "dca", "dca_conservative",
        "political_favourite", "political_favourite_aggr", "political_favourite_cons",
        "political_favourite_e10",
    ],
    "entertainment": [
        "srb_generic_res", "srb_generic_x5", "srb_generic_x10",
        "volatility_spread",
        "ladder_mm", "ladder_mm_wide",
        "auto_hedge",
    ],
    "science": [
        "srb_generic_res", "srb_generic_x5",
        "volatility_spread",
        "weather_fade", "weather_fade_aggr", "weather_fade_cons", "weather_fade_ultra",
    ],
    "default": [
        "srb_generic_res", "srb_generic_x5",
        "volatility_spread",
    ],
    # "other" catches everything unclassified — include weather_fade since
    # some weather/climate markets don't have recognizable keywords
    "other": [
        "srb_generic_res", "srb_generic_x5",
        "volatility_spread",
        "weather_fade", "weather_fade_aggr", "weather_fade_cons", "weather_fade_ultra",
    ],
}

# Minimum volume for high-quality strategies (stink_bid + volatility_spread)
MIN_VOLUME_HIGH_QUALITY = 30_000.0

# Size multiplier per category (research: Sports has 2.23% gap vs Finance 0.17%)
CATEGORY_SIZE_MULTIPLIER: dict[str, float] = {
    "sports": 1.5,
    "entertainment": 1.3,
    "politics": 1.0,
    "crypto": 1.0,
    "economics": 0.7,
    "science": 0.7,
    "default": 0.8,
}

# Minimum volume/liquidity to consider a market worth trading
MIN_VOLUME_USD = 1000.0
MIN_LIQUIDITY_USD = 100.0


def _safe_float(val: Any) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0

# ---------------------------------------------------------------------------
# Crypto-specific parsing (kept from original scanner)
# ---------------------------------------------------------------------------

_CRYPTO_PATTERNS: dict[str, re.Pattern[str]] = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE),
    "ETH": re.compile(r"\b(ethereum|eth)\b", re.IGNORECASE),
    "SOL": re.compile(r"\b(solana|sol)\b", re.IGNORECASE),
}

_THRESHOLD_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")
_ABOVE_PATTERN = re.compile(r"\b(above|over)\b", re.IGNORECASE)
_BELOW_PATTERN = re.compile(r"\b(below|under)\b", re.IGNORECASE)

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

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _is_rejected(question: str) -> bool:
    return any(p.search(question) for p in _REJECT_PATTERNS)


def _extract_crypto(question: str) -> str | None:
    for symbol, pattern in _CRYPTO_PATTERNS.items():
        if pattern.search(question):
            return symbol
    return None


def _extract_threshold(question: str) -> float | None:
    matches = _THRESHOLD_PATTERN.findall(question)
    if not matches:
        return None
    values = []
    for m in matches:
        try:
            values.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return max(values) if values else None


def _extract_direction(question: str) -> str | None:
    if _BELOW_PATTERN.search(question):
        return "BELOW"
    if _ABOVE_PATTERN.search(question):
        return "ABOVE"
    return None


def _extract_date(question: str) -> date | None:
    today = date.today()
    for pattern_str, has_year in [
        (r"\b(?:on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b", True),
        (r"\b(?:on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", False),
    ]:
        m = re.search(pattern_str, question, re.IGNORECASE)
        if m:
            month = _MONTH_MAP.get(m.group(1).lower())
            if month:
                year = int(m.group(3)) if has_year else today.year
                try:
                    d = date(year, month, int(m.group(2)))
                    if not has_year and d < today:
                        d = date(year + 1, month, int(m.group(2)))
                    return d
                except ValueError:
                    pass

    m = re.search(r"\b(?:on|by)\s+(\d{4}-\d{2}-\d{2})\b", question, re.IGNORECASE)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass

    m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", question, re.IGNORECASE)
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


def parse_market_question(question: str) -> dict[str, Any]:
    """Parse a Polymarket question string into structured fields."""
    result: dict[str, Any] = {
        "crypto": None, "threshold": None, "direction": None,
        "resolution_date": None, "parseable": False,
    }
    if _is_rejected(question):
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
    return result


def _detect_category(pm: PolymarketMarket) -> str:
    """Detect category from Gamma tags or market question."""
    tag_slugs = {t.get("slug", "").lower() for t in pm.tags if isinstance(t, dict)}

    # Check raw event data for category field
    raw_category = pm.raw.get("category", "").lower() if pm.raw else ""

    for category, tag_list in SCAN_CATEGORIES.items():
        for tag in tag_list:
            if tag in tag_slugs or tag in raw_category:
                return category

    # Fallback: check question keywords
    q = pm.question.lower()
    if any(w in q for w in ["bitcoin", "ethereum", "solana", "btc", "eth", "sol", "crypto"]):
        return "crypto"
    if any(w in q for w in ["nba", "nfl", "mlb", "soccer", "game", "match", "team", "win", "beat", "score", "points"]):
        return "sports"
    if any(w in q for w in ["election", "president", "congress", "senate", "vote", "trump", "biden", "poll"]):
        return "politics"
    if any(w in q for w in ["oscar", "grammy", "movie", "album", "show", "celebrity"]):
        return "entertainment"
    if any(w in q for w in ["temperature", "rainfall", "rain", "hurricane", "drought",
                             "flood", "celsius", "fahrenheit", "precipitation", "snow",
                             "tornado", "weather", "climate", "storm", "wind speed"]):
        return "science"

    return "other"


# ---------------------------------------------------------------------------
# MarketScanner
# ---------------------------------------------------------------------------


def _filter_live_markets(
    markets: list[PolymarketMarket],
) -> list[PolymarketMarket]:
    """Filter to only live, current markets with meaningful activity.

    Rejects:
    - Closed, resolved, or archived markets
    - Markets with end_date in the past
    - Markets with zero volume (dead/stale)
    - Markets below minimum volume threshold
    """
    now = datetime.now(timezone.utc)
    filtered = []

    for pm in markets:
        # Must be active and accepting orders
        if not pm.active or pm.closed or pm.archived:
            continue

        # Must have some volume (filters out dead markets)
        if pm.volume < MIN_VOLUME_USD:
            continue

        # End date must be in the future (if provided)
        if pm.end_date_iso:
            try:
                end = datetime.fromisoformat(pm.end_date_iso.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end < now:
                    continue
            except (ValueError, TypeError):
                pass  # no valid end date — allow through

        filtered.append(pm)

    logger.info(
        "_filter_live_markets: %d → %d (removed %d stale/dead)",
        len(markets), len(filtered), len(markets) - len(filtered),
    )
    return filtered


class MarketScanner:
    """Discovers and tracks Polymarket markets across all categories."""

    def __init__(self, gamma_client: GammaClient, db_session: AsyncSession) -> None:
        self._gamma = gamma_client
        self._db = db_session

    async def full_scan(self) -> dict[str, int]:
        """Full market scan — Monday 00:00 UTC. Scans all categories."""
        logger.info("MarketScanner: starting FULL scan (all categories)")
        stats = {"new": 0, "updated": 0, "skipped": 0}

        # 1. Scan crypto markets (original method — slug-based)
        crypto_stats = await self._scan_crypto_slugs()
        _merge_stats(stats, crypto_stats)

        # 2. Scan all other categories via tags
        for category, tags in SCAN_CATEGORIES.items():
            if category == "crypto":
                continue  # already handled above
            for tag in tags:
                try:
                    tag_stats = await self._scan_by_tag(tag, category)
                    _merge_stats(stats, tag_stats)
                except Exception as exc:
                    logger.warning("full_scan: tag=%s failed: %s", tag, exc)

        # 3. Check resolution only for markets with open positions
        await self._check_resolution_for_open_positions()

        # 4. Auto-assign strategies to new markets
        await self._auto_assign_strategies()

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
        """Quick scan — every 15 minutes. Crypto slugs + popular tags."""
        logger.info("MarketScanner: starting QUICK scan")
        stats = {"new": 0, "updated": 0, "skipped": 0}

        # Crypto slugs (original fast method)
        try:
            crypto_stats = await self._scan_crypto_slugs()
            _merge_stats(stats, crypto_stats)
        except Exception as exc:
            logger.warning("quick_scan: crypto slug scan failed: %s — rolling back and continuing", exc)
            await self._db.rollback()

        # Weather slugs — direct slug scan (Gamma tag=weather is broken)
        try:
            weather_stats = await self._scan_weather_slugs()
            _merge_stats(stats, weather_stats)
        except Exception as exc:
            logger.warning("quick_scan: weather slug scan failed: %s — rolling back and continuing", exc)
            await self._db.rollback()

        # Quick scan of high-priority categories (tag=API tag, category=internal key)
        for tag, category in [("Sports", "sports"), ("Politics", "politics")]:
            try:
                tag_stats = await self._scan_by_tag(tag, category, limit=50)
                _merge_stats(stats, tag_stats)
            except Exception as exc:
                logger.debug("quick_scan: tag=%s failed: %s", tag, exc)

        await self._check_resolution_for_open_positions()
        await self._auto_assign_strategies()

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
    # Scan methods
    # ------------------------------------------------------------------

    async def _scan_crypto_slugs(self) -> dict[str, int]:
        """Original crypto slug-based scanning for BTC/ETH/SOL price markets."""
        raw_markets: list[PolymarketMarket] = []

        _CRYPTO_SLUG_PREFIX = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
        _MONTH_SLUG = {
            1: "january", 2: "february", 3: "march", 4: "april",
            5: "may", 6: "june", 7: "july", 8: "august",
            9: "september", 10: "october", 11: "november", 12: "december",
        }
        today = date.today()
        for delta in range(-3, 8):  # 3 days back + 7 days forward to catch recent resolutions
            target_date = today + timedelta(days=delta)
            month_name = _MONTH_SLUG[target_date.month]
            for crypto in settings.target_cryptos:
                prefix = _CRYPTO_SLUG_PREFIX.get(crypto, crypto.lower())
                slug = f"{prefix}-above-on-{month_name}-{target_date.day}"
                try:
                    results = await self._gamma.get_markets_from_event_slug(slug)
                    if results:
                        logger.debug("Event slug %s: %d markets", slug, len(results))
                    raw_markets.extend(results)
                except Exception as exc:
                    logger.debug("Slug scan skip %s: %s", slug, exc)

        return await self._process_markets(raw_markets, force_category="crypto")

    async def _scan_weather_slugs(self) -> dict[str, int]:
        """Scan weather temperature markets via direct slug patterns.

        Gamma tag=weather is broken (returns unrelated markets). Instead we
        construct slugs directly: highest-temperature-in-{city}-on-{month}-{day}-{year}.
        Covers 18 cities, next 7 days.
        """
        _WEATHER_CITIES = [
            "nyc", "los-angeles", "chicago", "miami", "london", "paris",
            "tokyo", "toronto", "madrid", "seattle", "denver", "dallas",
            "atlanta", "singapore", "hong-kong", "seoul", "houston", "san-francisco",
        ]
        _MONTH_SLUG = {
            1: "january", 2: "february", 3: "march", 4: "april",
            5: "may", 6: "june", 7: "july", 8: "august",
            9: "september", 10: "october", 11: "november", 12: "december",
        }
        raw_markets: list[PolymarketMarket] = []
        today = date.today()
        for delta in range(0, 7):
            target_date = today + timedelta(days=delta)
            month_name = _MONTH_SLUG[target_date.month]
            for city in _WEATHER_CITIES:
                slug = f"highest-temperature-in-{city}-on-{month_name}-{target_date.day}-{target_date.year}"
                try:
                    results = await self._gamma.get_markets_from_event_slug(slug)
                    if results:
                        logger.debug("Weather slug %s: %d markets", slug, len(results))
                    raw_markets.extend(results)
                except Exception as exc:
                    logger.debug("Weather slug skip %s: %s", slug, exc)

        logger.info("_scan_weather_slugs: %d raw markets found", len(raw_markets))
        return await self._process_markets(raw_markets, force_category="science")

    async def _scan_by_tag(
        self, tag: str, category: str, limit: int = 200
    ) -> dict[str, int]:
        """Scan Gamma API events endpoint for markets with a specific tag.

        Uses the /events endpoint (not /markets) because the events endpoint
        returns current, well-categorized markets. Markets from events are
        then filtered for liveness, volume, and future end dates.
        """
        raw_events = await self._gamma.get_events(
            tag=tag, active=True, closed=False,
            limit=min(limit, 100), order="volume", ascending=False,
        )

        # Extract markets from events, inheriting event-level fields
        raw_markets: list[PolymarketMarket] = []
        from prophet.polymarket.gamma_client import _parse_gamma_market
        for event in raw_events:
            event_volume = _safe_float(event.get("volume") or event.get("competitionVolume"))
            event_end_date = event.get("endDate") or event.get("endDateIso")
            event_slug = event.get("slug") or ""
            for raw_market in event.get("markets", []):
                if isinstance(raw_market, dict):
                    try:
                        # Inherit event-level volume/endDate/slug if market lacks them
                        if not raw_market.get("volume") and event_volume:
                            raw_market["volume"] = event_volume
                        if not raw_market.get("endDateIso") and not raw_market.get("endDate") and event_end_date:
                            raw_market["endDateIso"] = event_end_date
                        if not raw_market.get("slug") and event_slug:
                            raw_market["slug"] = event_slug
                        raw_markets.append(_parse_gamma_market(raw_market))
                    except Exception:
                        continue

        # Filter: only live markets with volume and future end dates
        filtered = _filter_live_markets(raw_markets)
        logger.info(
            "_scan_by_tag(%s, %s): %d events → %d markets → %d after filter",
            tag, category, len(raw_events), len(raw_markets), len(filtered),
        )
        return await self._process_markets(filtered, force_category=category)

    # ------------------------------------------------------------------
    # Market processing
    # ------------------------------------------------------------------

    async def _process_markets(
        self, raw_markets: list[PolymarketMarket],
        force_category: str | None = None,
    ) -> dict[str, int]:
        """Upsert markets into DB."""
        from prophet.db.models import Market

        stats = {"new": 0, "updated": 0, "skipped": 0}
        seen: set[str] = set()

        for pm in raw_markets:
            condition_id = pm.condition_id or pm.id
            if not condition_id or condition_id in seen:
                continue
            seen.add(condition_id)

            token_yes = pm.token_id_yes
            token_no = pm.token_id_no
            if not token_yes or not token_no:
                stats["skipped"] += 1
                continue

            # Detect category: use force_category when provided (reliable
            # from slug/tag-based scan), fall back to question detection
            if force_category:
                category = force_category
            else:
                category = _detect_category(pm)

            # For crypto markets, parse the question for structured data
            parsed = {"crypto": None, "threshold": None, "direction": None, "resolution_date": None}
            if category == "crypto":
                parsed = parse_market_question(pm.question)
                if not parsed.get("parseable"):
                    stats["skipped"] += 1
                    continue
                if parsed["crypto"] not in settings.target_cryptos:
                    stats["skipped"] += 1
                    continue
            else:
                # Non-crypto: extract resolution_date from end_date_iso field
                if pm.end_date_iso:
                    try:
                        _end = datetime.fromisoformat(pm.end_date_iso.replace("Z", "+00:00"))
                        parsed["resolution_date"] = _end.date()
                    except (ValueError, TypeError):
                        pass

            # For non-crypto: require minimum volume AND liquidity
            if category != "crypto":
                if pm.volume < MIN_VOLUME_USD or pm.liquidity < MIN_LIQUIDITY_USD:
                    stats["skipped"] += 1
                    continue

            # Check if already exists — expire_all ensures we see committed DB state
            await self._db.flush()
            self._db.expire_all()
            existing = await self._get_market_by_condition_id(condition_id)

            if existing is None:
                market = Market(
                    condition_id=condition_id,
                    question=pm.question,
                    crypto=parsed.get("crypto"),
                    category=category,
                    threshold=parsed.get("threshold"),
                    direction=parsed.get("direction"),
                    resolution_date=parsed.get("resolution_date"),
                    token_id_yes=token_yes,
                    token_id_no=token_no,
                    status="active",
                    volume_usd=pm.volume or 0.0,
                    slug=pm.slug or None,
                )
                self._db.add(market)
                stats["new"] += 1
                logger.info(
                    "New market [%s]: %s | %s (vol=$%.0f)",
                    category, condition_id[:12], pm.question[:60], pm.volume or 0,
                )
            else:
                # Always update volume (it grows over time)
                if pm.volume:
                    existing.volume_usd = pm.volume

                # Always trust _detect_category result — it uses keyword detection
                # and is more reliable than the force_category from tag-based scans.
                if existing.category != category:
                    existing.category = category
                    stats["updated"] += 1

                # Backfill slug if missing
                if not existing.slug and pm.slug:
                    existing.slug = pm.slug

                # Backfill resolution_date if missing and we have end_date_iso
                if existing.resolution_date is None and pm.end_date_iso:
                    try:
                        _end = datetime.fromisoformat(pm.end_date_iso.replace("Z", "+00:00"))
                        existing.resolution_date = _end.date()
                    except (ValueError, TypeError):
                        pass

                # Update resolution
                changed = False
                if pm.resolved and pm.outcome and existing.status in ("active", "expired"):
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
                    logger.info("Market resolved: %s -> %s", condition_id[:12], existing.resolved_outcome)

                stats["updated" if changed else "skipped"] += 1

        try:
            await self._db.flush()
        except Exception as exc:
            logger.error("DB flush failed during market processing: %s", exc)
            raise

        return stats

    # ------------------------------------------------------------------
    # Auto-assign strategies
    # ------------------------------------------------------------------

    async def _auto_assign_strategies(self) -> None:
        """Ensure every active market has strategy configs assigned based on its category."""
        from prophet.db.models import Market, StrategyConfig

        # Get all active markets
        stmt = select(Market).where(Market.status == "active")
        result = await self._db.execute(stmt)
        active_markets = list(result.scalars().all())

        if not active_markets:
            return

        # Get existing strategy configs (global ones — no market_id)
        global_stmt = select(StrategyConfig).where(StrategyConfig.market_id.is_(None))
        global_result = await self._db.execute(global_stmt)
        global_configs = {cfg.strategy for cfg in global_result.scalars().all()}

        # Determine which strategies should be globally enabled
        all_needed: set[str] = set()
        for market in active_markets:
            cat = market.category or "default"
            strategies = CATEGORY_STRATEGIES.get(cat, CATEGORY_STRATEGIES["default"])
            all_needed.update(strategies)

        # Create missing global configs
        new_count = 0
        for strategy_name in all_needed:
            if strategy_name not in global_configs:
                cfg = StrategyConfig(
                    strategy=strategy_name,
                    market_id=None,
                    crypto=None,
                    enabled=True,
                    params={},
                )
                self._db.add(cfg)
                global_configs.add(strategy_name)
                new_count += 1

        if new_count:
            logger.info("Auto-assigned %d new strategy configs", new_count)
            try:
                await self._db.flush()
            except Exception as exc:
                logger.error("Strategy auto-assign flush failed: %s", exc)

    # ------------------------------------------------------------------
    # Resolution updates
    # ------------------------------------------------------------------

    async def _check_resolution_for_open_positions(self) -> None:
        """Check resolution for ALL markets with open positions via CLOB API.

        CLOB sets token.winner=True immediately when market resolves.
        Gamma delays resolved=True for hours/days, so we skip it entirely.
        """
        from prophet.db.models import Market, Position

        # Find ALL markets with open positions that aren't yet resolved
        stmt = (
            select(Market)
            .join(Position, Position.market_id == Market.id)
            .where(
                Position.status == "open",
                Market.status.in_(("active", "expired")),
                Market.resolved_outcome.is_(None),
            )
            .distinct()
        )
        result = await self._db.execute(stmt)
        all_markets = result.scalars().all()

        if not all_markets:
            return

        resolved_count = 0

        # --- Use CLOB API for ALL markets (Gamma often returns NOT FOUND) ---
        logger.debug("Checking CLOB resolution for %d markets with open positions", len(all_markets))
        from prophet.polymarket.clob_client import PolymarketClient
        clob = PolymarketClient()
        await clob.start()
        try:
            for market in all_markets:
                try:
                    resolved, outcome = await clob.get_market_resolution(market.condition_id)
                    if resolved and outcome:
                        market.status = "resolved"
                        market.resolved_outcome = outcome
                        market.resolution_time = datetime.now(timezone.utc)
                        resolved_count += 1
                        logger.info(
                            "Resolution confirmed (CLOB): market_id=%d %s -> %s",
                            market.id, market.condition_id[:12], outcome,
                        )
                except Exception as exc:
                    logger.debug("CLOB resolution check failed for market %d: %s", market.id, exc)
        finally:
            await clob.close()

        if resolved_count:
            try:
                await self._db.flush()
            except Exception as exc:
                logger.error("DB flush failed during resolution check: %s", exc)
                raise

    async def handle_new_market_event(self, condition_id: str, _raw_data: dict) -> None:
        """Handle a new_market WS event — process immediately without waiting for scan.

        Uses its OWN DB session (not self._db) to avoid conflicting with a
        concurrent full_scan or quick_scan that may be mid-transaction.
        Falls through silently on any error (WS events are best-effort).
        """
        try:
            from prophet.db.database import get_session
            from prophet.db.models import Market
            from sqlalchemy import select

            # Fast check with own session — skip if already in DB
            async with get_session() as check_db:
                existing = await check_db.scalar(
                    select(Market.id).where(Market.condition_id == condition_id)
                )
            if existing:
                return

            logger.info("WS new_market: fetching %s from Gamma", condition_id[:16])
            market = await self._gamma.get_market(condition_id)
            if not market:
                return

            # Persist with a dedicated short-lived session
            async with get_session() as ws_db:
                # Temporarily swap self._db so _process_markets / _auto_assign use this session
                original_db = self._db
                self._db = ws_db
                try:
                    stats = await self._process_markets([market])
                    if stats.get("new", 0):
                        await self._auto_assign_strategies()
                        await ws_db.commit()
                        logger.info("WS new_market: persisted condition=%s", condition_id[:16])
                finally:
                    self._db = original_db

        except Exception as exc:
            logger.warning("handle_new_market_event failed for %s: %s", condition_id[:16], exc)

    async def _get_market_by_condition_id(self, condition_id: str) -> Any | None:
        from prophet.db.models import Market
        stmt = select(Market).where(Market.condition_id == condition_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()


def _merge_stats(target: dict[str, int], source: dict[str, int]) -> None:
    for key in ("new", "updated", "skipped"):
        target[key] = target.get(key, 0) + source.get(key, 0)
