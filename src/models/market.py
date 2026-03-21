"""
PROPHET STRATEGIES
Market dataclass — represents a single Polymarket prediction market
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from enum import Enum


class CryptoAsset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"


class Direction(str, Enum):
    ABOVE = "above"
    BELOW = "below"
    HIT = "hit"


class PeriodType(str, Enum):
    ON_DATE = "on_date"
    BY_DATE = "by_date"


class Outcome(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


@dataclass
class Market:
    """A single Polymarket prediction market for crypto price."""
    condition_id: str
    question: str
    event_market_name: str

    # Parsed fields
    crypto: Optional[CryptoAsset] = None
    threshold: Optional[float] = None
    direction: Optional[Direction] = None
    resolution_date: Optional[date] = None
    period_type: Optional[PeriodType] = None

    # Resolution
    resolved_outcome: Outcome = Outcome.UNKNOWN
    resolution_time: Optional[datetime] = None

    # Metadata
    first_trade_time: Optional[datetime] = None
    last_trade_time: Optional[datetime] = None
    total_volume_usd: float = 0.0
    trade_count: int = 0
    neg_risk: bool = False

    def is_parsed(self) -> bool:
        """True if all fields were successfully extracted from question."""
        return all([
            self.crypto is not None,
            self.threshold is not None,
            self.direction is not None,
            self.resolution_date is not None,
        ])

    def is_resolved(self) -> bool:
        return self.resolved_outcome != Outcome.UNKNOWN

    def __repr__(self) -> str:
        return (
            f"Market({self.crypto} {self.direction} {self.threshold} "
            f"on {self.resolution_date} → {self.resolved_outcome})"
        )
