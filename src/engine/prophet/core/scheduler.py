"""
Scheduler — APScheduler-based periodic task runner.

:class:`Scheduler` owns and manages all recurring jobs:

┌──────────────────────────────┬────────────────────────────────┐
│ Job                          │ Interval / Trigger             │
├──────────────────────────────┼────────────────────────────────┤
│ scanner.full_scan            │ Every Monday 00:00 UTC (cron)  │
│ scanner.quick_scan           │ Every 15 minutes               │
│ data_collector.collect_prices│ Every 1 minute                 │
│ data_collector.collect_obs   │ Every 5 minutes                │
│ data_collector.collect_trades│ Every 2 minutes                │
│ signal_generator.run         │ Every 15 minutes               │
│ order_manager.check_fills    │ Every 2 minutes                │
│ order_manager.check_exits    │ Every 5 minutes                │
└──────────────────────────────┴────────────────────────────────┘

Error handling
--------------
Each job is wrapped in a try/except so a single failure cannot crash the
scheduler.  Errors are logged at ERROR level with the job name.

Usage
-----
    scheduler = Scheduler(scanner, data_collector, signal_generator, order_manager)
    await scheduler.start()
    # ... runs in background ...
    await scheduler.stop()
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages all periodic background jobs using APScheduler.

    Parameters
    ----------
    scanner:
        :class:`~prophet.core.scanner.MarketScanner` instance.
    data_collector:
        :class:`~prophet.core.data_collector.DataCollector` instance.
    signal_generator:
        :class:`~prophet.core.signal_generator.SignalGenerator` instance.
    order_manager:
        :class:`~prophet.core.order_manager.OrderManager` instance.
    """

    def __init__(
        self,
        scanner: Any,
        data_collector: Any,
        signal_generator: Any,
        order_manager: Any,
    ) -> None:
        self._scanner = scanner
        self._data_collector = data_collector
        self._signal_generator = signal_generator
        self._order_manager = order_manager

        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register all jobs and start the scheduler."""
        if self._running:
            logger.warning("Scheduler is already running")
            return

        self._register_jobs()
        self._scheduler.start()
        self._running = True
        logger.info("Scheduler started — %d jobs registered", len(self._scheduler.get_jobs()))

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        if not self._running:
            return

        self._scheduler.shutdown(wait=True)
        self._running = False
        logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        """True if the scheduler is currently running."""
        return self._running

    def get_job_status(self) -> list[dict[str, Any]]:
        """Return status info for all registered jobs."""
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs

    # ------------------------------------------------------------------
    # Job registration
    # ------------------------------------------------------------------

    def _register_jobs(self) -> None:
        """Register all scheduled jobs with APScheduler."""
        s = self._scheduler

        # ── Market Scanner ────────────────────────────────────────────
        # Full scan: Monday 00:00 UTC
        s.add_job(
            self._safe_run("scanner.full_scan", self._scanner.full_scan),
            trigger=CronTrigger(day_of_week="mon", hour=0, minute=0),
            id="scanner_full",
            name="MarketScanner — full scan (Monday)",
            replace_existing=True,
            misfire_grace_time=3600,  # allow up to 1h late start
        )

        # Quick scan: every 15 minutes
        s.add_job(
            self._safe_run("scanner.quick_scan", self._scanner.quick_scan),
            trigger=IntervalTrigger(minutes=15),
            id="scanner_quick",
            name="MarketScanner — quick scan",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # ── Data Collector ────────────────────────────────────────────
        # Prices: every 1 minute
        s.add_job(
            self._safe_run("data_collector.collect_prices", self._data_collector.collect_prices),
            trigger=IntervalTrigger(minutes=1),
            id="collect_prices",
            name="DataCollector — spot prices",
            replace_existing=True,
            misfire_grace_time=60,
        )

        # Order books: every 5 minutes
        s.add_job(
            self._safe_run("data_collector.collect_orderbooks", self._data_collector.collect_orderbooks),
            trigger=IntervalTrigger(minutes=5),
            id="collect_orderbooks",
            name="DataCollector — order book snapshots",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Trades: every 2 minutes
        s.add_job(
            self._safe_run("data_collector.collect_trades", self._data_collector.collect_trades),
            trigger=IntervalTrigger(minutes=2),
            id="collect_trades",
            name="DataCollector — observed trades",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Market status: every 15 minutes
        s.add_job(
            self._safe_run("data_collector.collect_market_status", self._data_collector.collect_market_status),
            trigger=IntervalTrigger(minutes=15),
            id="collect_market_status",
            name="DataCollector — market status",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # ── Signal Generator ──────────────────────────────────────────
        # Every 15 minutes, offset by 3 min so it doesn't collide with scanner
        import datetime as _dt
        _sig_start = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=3)
        s.add_job(
            self._safe_run("signal_generator.run", self._signal_generator.run),
            trigger=IntervalTrigger(minutes=15, start_date=_sig_start),
            id="signal_generation",
            name="SignalGenerator — evaluate markets",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # ── Order Manager ─────────────────────────────────────────────
        # Convert pending signals → paper orders: every 5 minutes
        s.add_job(
            self._safe_run("order_manager.place_pending_orders", self._order_manager.place_pending_orders),
            trigger=IntervalTrigger(minutes=5),
            id="place_pending_orders",
            name="OrderManager — place pending orders",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Fill checks: every 2 minutes
        s.add_job(
            self._safe_run("order_manager.check_fills", self._order_manager.check_fills),
            trigger=IntervalTrigger(minutes=2),
            id="check_fills",
            name="OrderManager — check fills",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # Exit checks: every 5 minutes
        s.add_job(
            self._safe_run("order_manager.check_exits", self._order_manager.check_exits),
            trigger=IntervalTrigger(minutes=5),
            id="check_exits",
            name="OrderManager — check exits",
            replace_existing=True,
            misfire_grace_time=180,
        )

        # ── Telegram Daily Summary ────────────────────────────────────
        # Every day at 20:00 UTC
        async def _daily_summary() -> None:
            from prophet.core.telegram_bot import notifier
            if notifier.enabled:
                await notifier.build_and_send_daily_summary()

        s.add_job(
            self._safe_run("telegram.daily_summary", _daily_summary),
            trigger=CronTrigger(hour=20, minute=0),
            id="telegram_daily_summary",
            name="Telegram — daily summary",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        logger.debug(
            "Registered %d scheduler jobs", len(s.get_jobs())
        )

    # ------------------------------------------------------------------
    # Error wrapper
    # ------------------------------------------------------------------

    def _safe_run(
        self, job_name: str, coro_fn: Callable[[], Coroutine[Any, Any, Any]]
    ) -> Callable[[], Coroutine[Any, Any, None]]:
        """Wrap an async job function to catch and log all exceptions.

        A single job failure must NOT propagate to the scheduler and cause
        other jobs to stop running.

        Parameters
        ----------
        job_name:
            Human-readable name for logging.
        coro_fn:
            The async callable to wrap (called with no arguments).

        Returns
        -------
        Callable
            An async function that is safe to pass to APScheduler.
        """
        async def _wrapper() -> None:
            try:
                logger.debug("Job starting: %s", job_name)
                result = await coro_fn()
                logger.debug("Job completed: %s → %s", job_name, result)
            except Exception as exc:
                logger.error(
                    "Job FAILED: %s — %s: %s",
                    job_name,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )

        # Preserve the original function name for APScheduler's repr
        _wrapper.__name__ = job_name
        _wrapper.__qualname__ = job_name
        return _wrapper
