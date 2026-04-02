"""
LiveRiskManager — hard limits for live trading.

Checks BEFORE placing a real order. All limits are additive — ALL must pass.

Limits
------
- max_daily_usd:       Max total capital deployed today (default $50)
- max_open_positions:  Max simultaneous open live positions (default 20)
- per_strategy_max_usd: Max capital in any single strategy today (default $20)
- max_single_order_usd: Max size of a single order (default $10)
- live_strategies:      Whitelist of strategy names allowed to trade live
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class LiveRiskManager:
    """Hard-limit risk gates for live order placement.

    All thresholds are configurable via .env / settings but have safe defaults.
    """

    def __init__(
        self,
        max_daily_usd: float = 50.0,
        max_open_positions: int = 20,
        per_strategy_max_usd: float = 20.0,
        max_single_order_usd: float = 10.0,
        live_strategies: list[str] | None = None,
    ) -> None:
        self.max_daily_usd = max_daily_usd
        self.max_open_positions = max_open_positions
        self.per_strategy_max_usd = per_strategy_max_usd
        self.max_single_order_usd = max_single_order_usd
        # Default: only the 3 validated strategies
        self.live_strategies: set[str] = set(
            live_strategies or ["srb_cheap_x5", "srb_cheap_x10", "srb_mid_x3"]
        )

    async def check(
        self,
        db: AsyncSession,
        strategy: str,
        size_usd: float,
    ) -> tuple[bool, str]:
        """Gate check before placing a live order.

        Returns
        -------
        (allowed, reason)
            allowed=True means the order can proceed.
            allowed=False means it should be blocked, with reason explaining why.
        """
        from prophet.live.live_models import LiveOrder, LivePosition

        # 1. Strategy whitelist
        if strategy not in self.live_strategies:
            return False, f"Strategy {strategy!r} not in live whitelist"

        # 2. Single order size
        if size_usd > self.max_single_order_usd:
            return False, f"Order size ${size_usd:.2f} > max_single_order ${self.max_single_order_usd:.2f}"

        # 3. Open positions count
        open_count = await db.scalar(
            select(func.count(LivePosition.id)).where(LivePosition.status == "open")
        )
        if (open_count or 0) >= self.max_open_positions:
            return False, f"Open live positions {open_count} >= max {self.max_open_positions}"

        # 4. Daily spend
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_spend = await db.scalar(
            select(func.coalesce(func.sum(LivePosition.size_usd), 0.0)).where(
                LivePosition.opened_at >= today_start
            )
        )
        if (daily_spend or 0.0) + size_usd > self.max_daily_usd:
            return False, (
                f"Daily spend ${(daily_spend or 0.0):.2f} + ${size_usd:.2f} "
                f"would exceed max ${self.max_daily_usd:.2f}"
            )

        # 5. Per-strategy daily spend
        strategy_spend = await db.scalar(
            select(func.coalesce(func.sum(LivePosition.size_usd), 0.0)).where(
                LivePosition.opened_at >= today_start,
                LivePosition.strategy == strategy,
            )
        )
        if (strategy_spend or 0.0) + size_usd > self.per_strategy_max_usd:
            return False, (
                f"Strategy {strategy!r} daily spend "
                f"${(strategy_spend or 0.0):.2f} + ${size_usd:.2f} "
                f"would exceed per-strategy max ${self.per_strategy_max_usd:.2f}"
            )

        return True, "ok"

    def summary(self) -> dict[str, Any]:
        return {
            "max_daily_usd": self.max_daily_usd,
            "max_open_positions": self.max_open_positions,
            "per_strategy_max_usd": self.per_strategy_max_usd,
            "max_single_order_usd": self.max_single_order_usd,
            "live_strategies": sorted(self.live_strategies),
        }
