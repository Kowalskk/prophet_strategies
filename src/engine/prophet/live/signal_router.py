"""
SignalRouter — decides whether a signal goes to paper or live trading.

Routing logic
-------------
1. If settings.paper_trading is True → always paper (hard block)
2. If strategy is not in live_risk.live_strategies → paper
3. All checks pass → live

The router is called from order_manager.place_pending_orders() BEFORE
creating a PaperOrder, so the signal can be diverted to live instead.

Usage
-----
    router = SignalRouter(live_trader, risk_manager)
    route = await router.route(signal)
    if route == "live":
        await live_trader.place_live_order(db, signal, token_id)
    else:
        await order_manager.create_paper_order(db, signal)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from prophet.config import settings

if TYPE_CHECKING:
    from prophet.live.live_trader import LiveTrader
    from prophet.live.live_risk import LiveRiskManager

logger = logging.getLogger(__name__)


class SignalRouter:
    """Routes signals to paper or live trading.

    Parameters
    ----------
    live_trader:
        LiveTrader instance (used for actual placement).
    risk_manager:
        LiveRiskManager (used to check the whitelist quickly without DB).
    """

    def __init__(
        self,
        live_trader: LiveTrader,
        risk_manager: LiveRiskManager,
    ) -> None:
        self._trader = live_trader
        self._risk = risk_manager

    def route(self, signal: Any) -> str:
        """Synchronously determine routing for a signal.

        Returns "live" or "paper".
        Risk DB queries (daily limits) are deferred to place_live_order().
        """
        if settings.paper_trading:
            return "paper"

        if signal.strategy not in self._risk.live_strategies:
            return "paper"

        return "live"

    async def dispatch(
        self,
        db: Any,
        signal: Any,
        order_manager: Any,
        token_id: str | None = None,
    ) -> str:
        """Route and dispatch a signal. Returns "live" or "paper".

        Parameters
        ----------
        db:
            Active AsyncSession.
        signal:
            Signal ORM object.
        order_manager:
            OrderManager instance (for paper fallback).
        token_id:
            CLOB token ID — required for live trading. If None and route=live,
            falls back to paper.
        """
        route = self.route(signal)

        if route == "live":
            if not token_id:
                logger.warning(
                    "[ROUTER] No token_id for signal_id=%d strategy=%s — falling back to paper",
                    signal.id, signal.strategy,
                )
                route = "paper"
            else:
                result = await self._trader.place_live_order(db, signal, token_id)
                if result is None:
                    # Blocked by risk or CLOB error — fall through to paper
                    logger.info(
                        "[ROUTER] Live order blocked for signal_id=%d — falling back to paper",
                        signal.id,
                    )
                    route = "paper"
                else:
                    signal.status = "executed"
                    return "live"

        # Paper path
        await order_manager.create_paper_order(db, signal)
        return "paper"
