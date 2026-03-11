"""
PROPHET STRATEGIES
Market resolver — parses Polymarket question strings into structured Market objects
"""
from __future__ import annotations
import logging
import re
from datetime import date, datetime
from typing import Optional

from models.market import CryptoAsset, Direction, Market, Outcome, PeriodType

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Regex patterns
# ------------------------------------------------------------------

# Crypto detection
CRYPTO_PATTERNS = {
    CryptoAsset.BTC: re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE),
    CryptoAsset.ETH: re.compile(r"\b(ethereum|eth)\b", re.IGNORECASE),
    CryptoAsset.SOL: re.compile(r"\b(solana|sol)\b", re.IGNORECASE),
}

# Price threshold: $74,000 or $74000 or $5,000
THRESHOLD_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")

# Direction keywords — only strict "above/below" style, NOT "hit" or "reach"
ABOVE_PATTERN = re.compile(r"\b(above|over)\b", re.IGNORECASE)
BELOW_PATTERN = re.compile(r"\b(below|under)\b", re.IGNORECASE)

# Reject patterns — markets we never want even if they pass the SQL filter
REJECT_PATTERNS = [
    re.compile(r"\bhit\b", re.IGNORECASE),
    re.compile(r"\bor\b.{0,20}\bfirst\b", re.IGNORECASE),
    re.compile(r"\bin 202\d\b", re.IGNORECASE),
    re.compile(r"\bagain\b", re.IGNORECASE),
    re.compile(r"\bever\b", re.IGNORECASE),
    re.compile(r"\btoday\b", re.IGNORECASE),
    re.compile(r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b", re.IGNORECASE),
]

# Date extraction — multiple formats
# "on March 3", "on March 3rd", "by December 31", "on Feb 5"
DATE_PATTERNS = [
    # "on March 3, 2025" or "on March 3 2025"
    re.compile(r"\b(on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b", re.IGNORECASE),
    # "on March 3" (no year)
    re.compile(r"\b(on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE),
    # "on 2025-03-03"
    re.compile(r"\b(on|by)\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE),
    # "Mar 3" or "Mar 3rd"
    re.compile(r"\b([A-Za-z]{3})\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE),
]

MONTH_MAP = {
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


class MarketParser:
    """Parses Polymarket question strings into structured Market objects."""

    def parse(self, market: Market) -> Market:
        """Parse market in-place. Returns the same Market object."""
        q = market.question

        # Hard reject — wrong market type entirely
        for pattern in REJECT_PATTERNS:
            if pattern.search(q):
                logger.debug(f"Rejected market: {q!r}")
                return market  # leaves all fields None → is_parsed() = False

        market.crypto = self._extract_crypto(q)
        market.threshold = self._extract_threshold(q)
        market.direction = self._extract_direction(q)
        resolution_date, period_type = self._extract_date(q)
        market.resolution_date = resolution_date
        market.period_type = period_type

        if not market.is_parsed():
            logger.debug(f"Could not fully parse: {q!r}")

        return market

    def _extract_crypto(self, question: str) -> Optional[CryptoAsset]:
        for crypto, pattern in CRYPTO_PATTERNS.items():
            if pattern.search(question):
                return crypto
        return None

    def _extract_threshold(self, question: str) -> Optional[float]:
        matches = THRESHOLD_PATTERN.findall(question)
        if not matches:
            return None
        # Take the largest price (usually the threshold, not a fee or small number)
        values = []
        for m in matches:
            try:
                values.append(float(m.replace(",", "")))
            except ValueError:
                pass
        if not values:
            return None
        # For price thresholds, pick the largest value (e.g. $74,000 not $5)
        return max(values)

    def _extract_direction(self, question: str) -> Optional[Direction]:
        if BELOW_PATTERN.search(question):
            return Direction.BELOW
        if ABOVE_PATTERN.search(question):
            return Direction.ABOVE
        return None

    def _extract_date(self, question: str) -> tuple[Optional[date], Optional[PeriodType]]:
        """Try multiple patterns to extract resolution date."""
        
        # Pattern 1: "on/by MonthName Day, Year"
        m = re.search(
            r"\b(on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b",
            question, re.IGNORECASE
        )
        if m:
            period_type = PeriodType.ON_DATE if m.group(1).lower() == "on" else PeriodType.BY_DATE
            month = MONTH_MAP.get(m.group(2).lower())
            if month:
                try:
                    d = date(int(m.group(4)), month, int(m.group(3)))
                    return d, period_type
                except ValueError:
                    pass

        # Pattern 2: "on/by MonthName Day" (no year — infer from context)
        m = re.search(
            r"\b(on|by)\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            question, re.IGNORECASE
        )
        if m:
            period_type = PeriodType.ON_DATE if m.group(1).lower() == "on" else PeriodType.BY_DATE
            month = MONTH_MAP.get(m.group(2).lower())
            if month:
                # Infer year: use current year, but if month already passed use next year
                today = date.today()
                year = today.year
                try:
                    d = date(year, month, int(m.group(3)))
                    if d < today:
                        d = date(year + 1, month, int(m.group(3)))
                    return d, period_type
                except ValueError:
                    pass

        # Pattern 3: ISO date "on 2025-03-03"
        m = re.search(r"\b(on|by)\s+(\d{4}-\d{2}-\d{2})\b", question, re.IGNORECASE)
        if m:
            period_type = PeriodType.ON_DATE if m.group(1).lower() == "on" else PeriodType.BY_DATE
            try:
                d = date.fromisoformat(m.group(2))
                return d, period_type
            except ValueError:
                pass

        # Pattern 4: "Mar 3" or "March 3rd" without on/by (looser match)
        m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", question, re.IGNORECASE)
        if m:
            month = MONTH_MAP.get(m.group(1).lower())
            if month:
                today = date.today()
                year = today.year
                try:
                    d = date(year, month, int(m.group(2)))
                    if d < today:
                        d = date(year + 1, month, int(m.group(2)))
                    return d, PeriodType.ON_DATE
                except ValueError:
                    pass

        return None, None


def parse_resolution(payout_numerators: list) -> Outcome:
    """Convert Dune payoutNumerators to Outcome enum."""
    if not payout_numerators or len(payout_numerators) < 2:
        return Outcome.UNKNOWN
        
    # Standard Polymarket/Gnosis outcomes: 
    # [1000000000000000000, 0] = YES
    # [0, 1000000000000000000] = NO
    try:
        # Payouts from Dune might be strings if they are large uint256
        vals = [int(v) for v in payout_numerators]
    except (ValueError, TypeError):
        return Outcome.UNKNOWN
        
    if vals[0] > 0 and vals[1] == 0:
        return Outcome.YES
    if vals[1] > 0 and vals[0] == 0:
        return Outcome.NO
        
    return Outcome.UNKNOWN
