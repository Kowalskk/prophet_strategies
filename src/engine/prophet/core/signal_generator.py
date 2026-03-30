"""
Signal Generator — evaluates active markets against assigned strategies.

:class:`SignalGenerator` is the heart of the trading loop:

1. Query the DB for all active markets.
2. For each market, look up its assigned strategies in ``strategy_configs``.
3. For each strategy, fetch the current order book and spot price.
4. Call ``strategy.evaluate(market, orderbook, spot_price, params)``.
5. Pass each generated signal through :class:`~prophet.core.risk_manager.RiskManager`.
6. Persist approved signals to the ``signals`` table.
7. Return the list of new approved signals.

Strategy config resolution hierarchy (most specific wins):
  global default  →  per-crypto default  →  per-market override
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prophet.polymarket.clob_client import PolymarketClient
from prophet.polymarket.orderbook import OrderBookService
from prophet.polymarket.price_feeds import PriceFeedService
from prophet.strategies.base import TradeSignal
from prophet.strategies.registry import get_strategy, STRATEGY_REGISTRY
from prophet.core.scanner import CATEGORY_STRATEGIES, CATEGORY_SIZE_MULTIPLIER

if TYPE_CHECKING:
    from prophet.core.risk_manager import RiskManager

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SignalGenerator:
    """Evaluates active markets and generates trade signals.

    Parameters
    ----------
    clob_client:
        Started :class:`~prophet.polymarket.clob_client.PolymarketClient`.
    db_session:
        SQLAlchemy async session.
    risk_manager:
        :class:`~prophet.core.risk_manager.RiskManager` instance.
    redis_client:
        Async Redis client (used to read cached order books).  May be None.
    """

    def __init__(
        self,
        clob_client: PolymarketClient,
        db_session: AsyncSession,
        risk_manager: "RiskManager",
        redis_client: Any | None = None,
    ) -> None:
        self._clob = clob_client
        self._db = db_session
        self._risk = risk_manager
        self._redis = redis_client

        self._ob_service = OrderBookService(
            clob_client=clob_client,
            db_session=None,  # don't persist snapshots here
            redis_client=redis_client,
        )
        self._price_service = PriceFeedService(
            db_session=None,
            redis_client=redis_client,
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def run(self) -> list[Any]:
        """Run one signal generation cycle.

        Returns
        -------
        list
            Newly created :class:`~prophet.db.models.Signal` ORM rows.
        """
        from prophet.db.models import Market, StrategyConfig

        # Fetch all active markets
        stmt = select(Market).where(Market.status == "active")
        result = await self._db.execute(stmt)
        markets = list(result.scalars().all())

        if not markets:
            logger.debug("SignalGenerator: no active markets")
            return []

        logger.info("SignalGenerator: evaluating %d active markets", len(markets))

        new_signals: list[Any] = []

        for market in markets:
            try:
                market_signals = await self._evaluate_market(market)
                new_signals.extend(market_signals)
            except Exception as exc:
                logger.error(
                    "SignalGenerator: error evaluating market_id=%d: %s",
                    market.id, exc,
                )

        logger.info(
            "SignalGenerator: cycle complete — %d new signal(s)", len(new_signals)
        )
        try:
            await self._db.commit()
        except Exception as exc:
            logger.error("SignalGenerator: commit failed: %s", exc)
            await self._db.rollback()
        return new_signals

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _evaluate_market(self, market: Any) -> list[Any]:
        """Evaluate all assigned strategies for one market."""
        from prophet.db.models import StrategyConfig

        # Look up strategy assignments for this market
        strategy_configs = await self._get_strategy_configs(market)
        if not strategy_configs:
            logger.debug(
                "SignalGenerator: no strategies assigned to market_id=%d", market.id
            )
            return []

        # Fetch current order book (try cache first, else live fetch)
        orderbook = await self._get_orderbook(market)
        if not orderbook:
            category = getattr(market, "category", None) or "crypto"
            if category == "crypto":
                # Crypto markets must have orderbook data
                logger.warning(
                    "SignalGenerator: could not get order book for market_id=%d", market.id
                )
                return []
            # Non-crypto: strategies use last_trade_price from WS, not deep OB data
            # Pass an empty orderbook stub so strategies can still run
            orderbook = {"yes": None, "no": None}

        # Fetch current spot price for this market's crypto
        spot_price = await self._get_spot_price(market.crypto)

        new_db_signals: list[Any] = []

        # Category-aware filtering: only run strategies assigned to this category
        category = getattr(market, "category", None) or "crypto"
        allowed = set(CATEGORY_STRATEGIES.get(category, CATEGORY_STRATEGIES["default"]))
        size_mult = CATEGORY_SIZE_MULTIPLIER.get(category, CATEGORY_SIZE_MULTIPLIER["default"])

        for config in strategy_configs:
            if not config.enabled:
                continue

            strategy_name = config.strategy
            if strategy_name not in allowed:
                continue
            if strategy_name not in STRATEGY_REGISTRY:
                logger.warning("Unknown strategy in config: %r", strategy_name)
                continue

            try:
                strategy = get_strategy(strategy_name)
                params = strategy.validate_params(config.params or {})
                signals: list[TradeSignal] = await strategy.evaluate(
                    market, orderbook, spot_price, params
                )
            except Exception as exc:
                logger.error(
                    "Strategy %r raised an error for market_id=%d: %s",
                    strategy_name, market.id, exc,
                )
                continue

            for signal in signals:
                signal.strategy = strategy_name
                # Apply category-based size multiplier
                signal.size_usd = round(signal.size_usd * size_mult, 2)
                db_signal = await self._process_signal(signal, params)
                if db_signal is not None:
                    new_db_signals.append(db_signal)

        return new_db_signals

    async def _process_signal(
        self, signal: TradeSignal, params: dict[str, Any]
    ) -> Any | None:
        """Check a signal with RiskManager and persist if approved."""
        from prophet.db.models import Signal

        # Risk check
        approved, reason = await self._risk.check(signal)
        if not approved:
            logger.info(
                "Signal REJECTED by risk manager: market_id=%d %s %s@%.4f — %s",
                signal.market_id, signal.strategy, signal.side,
                signal.target_price, reason,
            )
            return None

        # LLM pre-trade filter (optional, non-blocking on errors)
        try:
            from prophet.core.llm_filter import llm_filter
            if llm_filter.enabled:
                from prophet.db.models import Market
                mkt_stmt = select(Market.question).where(Market.id == signal.market_id)
                mkt_result = await self._db.execute(mkt_stmt)
                question = mkt_result.scalar_one_or_none() or f"Market #{signal.market_id}"

                llm_ok, llm_reason = await llm_filter.evaluate(
                    market_question=question,
                    strategy_name=signal.strategy,
                    side=signal.side,
                    target_price=signal.target_price,
                    size_usd=signal.size_usd,
                )
                if not llm_ok:
                    logger.info(
                        "Signal REJECTED by LLM filter: market_id=%d %s %s@%.4f — %s",
                        signal.market_id, signal.strategy, signal.side,
                        signal.target_price, llm_reason,
                    )
                    return None
        except Exception as exc:
            logger.debug("LLM filter skipped: %s", exc)

        # Persist signal
        try:
            db_signal = Signal(
                market_id=signal.market_id,
                strategy=signal.strategy,
                side=signal.side,
                target_price=signal.target_price,
                size_usd=signal.size_usd,
                confidence=signal.confidence,
                params={
                    **params,
                    "exit_strategy": signal.exit_strategy,
                    "exit_params": signal.exit_params,
                    "metadata": signal.metadata,
                },
                status="pending",
                created_at=_utcnow(),
            )
            self._db.add(db_signal)
            await self._db.flush()

            logger.info(
                "Signal APPROVED: id=%d market_id=%d %s %s@%.4f size=$%.2f conf=%.2f",
                db_signal.id, signal.market_id, signal.strategy,
                signal.side, signal.target_price, signal.size_usd, signal.confidence,
            )
            return db_signal
        except Exception as exc:
            logger.error("Failed to persist signal: %s", exc)
            return None

    async def _get_strategy_configs(self, market: Any) -> list[Any]:
        """Return strategy configs for a market, using the resolution hierarchy.

        Priority (most specific wins): market-level > crypto-level > global.
        Returns one config per strategy (the most specific available).
        """
        from prophet.db.models import StrategyConfig

        # Fetch all configs applicable to this market
        stmt = select(StrategyConfig).where(
            (StrategyConfig.market_id == market.id)
            | (
                (StrategyConfig.market_id.is_(None))
                & (
                    (StrategyConfig.crypto == market.crypto)
                    | (StrategyConfig.crypto.is_(None))
                )
            )
        )
        result = await self._db.execute(stmt)
        all_configs = list(result.scalars().all())

        # Resolution: for each strategy, pick the most specific config
        per_strategy: dict[str, Any] = {}
        for cfg in all_configs:
            key = cfg.strategy
            existing = per_strategy.get(key)
            if existing is None:
                per_strategy[key] = cfg
            else:
                # More specific = higher specificity score
                def specificity(c: Any) -> int:
                    if c.market_id is not None:
                        return 2
                    if c.crypto is not None:
                        return 1
                    return 0

                if specificity(cfg) > specificity(existing):
                    per_strategy[key] = cfg

        return list(per_strategy.values())

    async def _get_orderbook(self, market: Any) -> dict[str, Any] | None:
        """Fetch order book for YES and NO sides (cache → live for crypto only)."""
        try:
            # Try Redis cache first
            yes_book = await self._ob_service.get_cached_book(market.id, "yes")
            no_book = await self._ob_service.get_cached_book(market.id, "no")

            if yes_book is None or no_book is None:
                category = getattr(market, "category", None) or "crypto"
                if category != "crypto":
                    # Non-crypto: don't do live fetch (too slow for 1000+ markets).
                    # The snapshot job populates cache for top non-crypto markets.
                    return None
                # Crypto: live fetch as fallback
                yes_book = await self._ob_service.fetch_and_compute(market.token_id_yes)
                no_book = await self._ob_service.fetch_and_compute(market.token_id_no)

            return {"yes": yes_book, "no": no_book}
        except Exception as exc:
            logger.warning(
                "_get_orderbook failed for market_id=%d: %s", market.id, exc
            )
            return None

    async def _get_spot_price(self, crypto: str | None) -> float:
        """Return the latest spot price for a crypto symbol.

        Falls back to 0.0 if price data is unavailable.
        """
        if not crypto:
            return 0.0
        try:
            await self._price_service.start()
            price_data = await self._price_service.get_cached(crypto)
            if price_data is not None:
                return price_data.price_usd
            # Cache miss — fetch live
            all_prices = await self._price_service.fetch_all()
            p = all_prices.get(crypto)
            return p.price_usd if p else 0.0
        except Exception as exc:
            logger.warning("_get_spot_price(%s) failed: %s", crypto, exc)
            return 0.0
