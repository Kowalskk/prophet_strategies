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
    "sports": ["Sports", "NBA", "NFL", "MLB", "Soccer", "MMA", "UFC", "Tennis"],
    "politics": ["Politics", "Elections", "Congress"],
    "entertainment": ["Entertainment", "Pop Culture", "Media"],
    "science": ["Science", "Climate", "Weather"],
    "economics": ["Economics", "Fed", "Macro"],
}

# Strategy assignments per category (based on Becker 2026 research)
CATEGORY_STRATEGIES: dict[str, list[str]] = {
    "crypto": [
        "srb_cheap_res", "srb_cheap_x5", "srb_cheap_x10",
        "srb_mid_res", "srb_mid_x3", "srb_mid_x5",
        "srb_high_res", "srb_high_x2", "srb_high_x4",
        "volatility_spread", "vs_x3", "vs_x4", "vs_x5",
        "liquidity_sniper",
    ],
    "sports": [
        "srb_generic_res", "srb_generic_x5", "srb_generic_x10",
        "dca", "dca_sports",
        "reversal", "reversal_aggressive",
    ],
    "politics": [
        "srb_generic_res", "srb_generic_x5",
        "pre_window", "pre_window_early", "pre_window_late",
        "dca", "dca_conservative",
        "political_favourite", "political_favourite_aggr", "political_favourite_cons",
    ],
    "entertainment": [
        "srb_generic_res", "srb_generic_x5", "srb_generic_x10",
        "ladder_mm", "ladder_mm_wide",
        "auto_hedge",
    ],
    "science": [
        "srb_generic_res", "srb_generic_x5",
        "weather_fade", "weather_fade_aggr", "weather_fade_cons",
    ],
    "default": [
        "srb_generic_res", "srb_generic_x5",
    ],
}

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

        # 3. Update resolved markets
        await self._update_resolved_markets()

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
        crypto_stats = await self._scan_crypto_slugs()
        _merge_stats(stats, crypto_stats)

        # Quick scan of high-priority categories
        for tag in ["sports", "politics"]:
            try:
                tag_stats = await self._scan_by_tag(tag, tag, limit=50)
                _merge_stats(stats, tag_stats)
            except Exception as exc:
                logger.debug("quick_scan: tag=%s failed: %s", tag, exc)

        await self._update_resolved_markets()
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
        for delta in range(0, 8):
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

    async def _scan_by_tag(
        self, tag: str, category: str, limit: int = 200
    ) -> dict[str, int]:
        """Scan Gamma API events endpoint for markets with a specific tag.

        Uses the /events endpoint (not /markets) because the events endpoint
        returns current, well-categorized markets. Markets from events are
        then filtered for liveness, volume, and future end dates.
        """
        raw_events = await self._gamma.get_events(
            tag=tag, active=True, limit=min(limit, 100)
        )

        # Extract markets from events, inheriting event-level fields
        raw_markets: list[PolymarketMarket] = []
        from prophet.polymarket.gamma_client import _parse_gamma_market
        for event in raw_events:
            event_volume = _safe_float(event.get("volume") or event.get("competitionVolume"))
            event_end_date = event.get("endDate") or event.get("endDateIso")
            for raw_market in event.get("markets", []):
                if isinstance(raw_market, dict):
                    try:
                        # Inherit event-level volume/endDate if market lacks them
                        if not raw_market.get("volume") and event_volume:
                            raw_market["volume"] = event_volume
                        if not raw_market.get("endDateIso") and not raw_market.get("endDate") and event_end_date:
                            raw_market["endDateIso"] = event_end_date
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

            # Detect category
            category = force_category or _detect_category(pm)

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

            # For non-crypto: require minimum volume AND liquidity
            if category != "crypto":
                if pm.volume < MIN_VOLUME_USD or pm.liquidity < MIN_LIQUIDITY_USD:
                    stats["skipped"] += 1
                    continue

            # Check if already exists
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
                )
                self._db.add(market)
                stats["new"] += 1
                logger.info(
                    "New market [%s]: %s | %s",
                    category, condition_id[:12], pm.question[:60],
                )
            else:
                # Update category if missing
                if existing.category is None and category:
                    existing.category = category

                # Update resolution
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

    async def _update_resolved_markets(self) -> None:
        """Check all active DB markets against Gamma for resolution updates."""
        from prophet.db.models import Market

        stmt = select(Market).where(Market.status == "active")
        result = await self._db.execute(stmt)
        active_markets = result.scalars().all()

        if not active_markets:
            return

        logger.debug("Checking resolution status for %d active markets", len(active_markets))

        for market in active_markets:
            try:
                resolution = await self._gamma.get_market_resolution(market.condition_id)
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
                        "Resolution update: market_id=%d %s -> %s",
                        market.id, market.condition_id[:12], market.resolved_outcome,
                    )
            except Exception as exc:
                logger.warning(
                    "Could not check resolution for market %s: %s",
                    market.condition_id[:12], exc,
                )

        try:
            await self._db.flush()
        except Exception as exc:
            logger.error("DB flush failed during resolution update: %s", exc)
            raise

    async def _get_market_by_condition_id(self, condition_id: str) -> Any | None:
        from prophet.db.models import Market
        stmt = select(Market).where(Market.condition_id == condition_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()


def _merge_stats(target: dict[str, int], source: dict[str, int]) -> None:
    for key in ("new", "updated", "skipped"):
        target[key] = target.get(key, 0) + source.get(key, 0)
