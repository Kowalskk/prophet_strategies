"""
FastAPI application factory for the Prophet engine REST API.

Startup sequence
----------------
1. Initialise the database engine (``init_db``).
2. Start the APScheduler background scheduler.

Shutdown sequence
-----------------
1. Stop the scheduler.
2. Dispose the database engine (``close_db``).

Routers are mounted under ``/api/v1``.
Authentication is handled by :class:`~prophet.api.middleware.BearerTokenMiddleware`.
CORS is configured via :class:`starlette.middleware.cors.CORSMiddleware`.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from prophet.api.middleware import CORS_KWARGS, BearerTokenMiddleware
from prophet.api.routes import config, data, markets, performance, positions, signals, strategies, system

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Manage startup and shutdown of background services."""
    # ---- STARTUP ----
    logger.info("Prophet Engine starting up…")

    # Initialise DB engine (no table creation — use Alembic)
    from prophet.db.database import init_db

    await init_db(create_tables=False)

    # Attempt to start the scheduler (best-effort; skip if dependencies missing)
    _scheduler = None
    try:
        from prophet.db.database import get_session
        from prophet.core.scheduler import Scheduler
        from prophet.core.scanner import MarketScanner
        from prophet.core.data_collector import DataCollector
        from prophet.core.signal_generator import SignalGenerator
        from prophet.core.order_manager import OrderManager
        from prophet.core.risk_manager import RiskManager
        from prophet.polymarket.gamma_client import GammaClient
        from prophet.polymarket.clob_client import PolymarketClient

        # Build shared HTTP clients
        gamma_client = GammaClient()
        await gamma_client.start()

        clob_client = PolymarketClient()
        await clob_client.start()

        # Each scheduler component gets its OWN session to avoid cross-job
        # session state conflicts (e.g. "Session is already flushing").
        from prophet.db.database import get_session_factory
        _sf = get_session_factory()

        _db_scanner = _sf()
        _db_signal  = _sf()
        _db_order   = _sf()

        scanner = MarketScanner(gamma_client=gamma_client, db_session=_db_scanner)
        # DataCollector now creates its own session per job call — no shared session
        data_collector = DataCollector(
            clob_client=clob_client,
            redis_client=None,
        )
        risk_mgr = RiskManager(db_session=_db_signal)
        signal_gen = SignalGenerator(
            clob_client=clob_client,
            db_session=_db_signal,
            risk_manager=risk_mgr,
        )
        order_mgr = OrderManager(
            clob_client=clob_client,
            db_session=_db_order,
        )

        _scheduler = Scheduler(
            scanner=scanner,
            data_collector=data_collector,
            signal_generator=signal_gen,
            order_manager=order_mgr,
        )
        await _scheduler.start()

        # Store scheduler on app state so shutdown can stop it
        app.state.scheduler = _scheduler
        app.state.db_session = _db_signal
        app.state.gamma_client = gamma_client
        app.state.clob_client = clob_client
        logger.info("Scheduler started.")
    except Exception as exc:
        logger.warning(
            "Scheduler could not start (non-fatal — API still available): %s", exc
        )
        app.state.scheduler = None

    # Start Telegram notifier (best-effort; non-fatal if not configured)
    try:
        from prophet.core.telegram_bot import notifier as tg_notifier
        await tg_notifier.start()
        app.state.telegram = tg_notifier
        if tg_notifier.enabled:
            await tg_notifier._send("🚀 <b>Prophet Engine started</b>")
            logger.info("TelegramNotifier started.")
    except Exception as exc:
        logger.warning("TelegramNotifier could not start: %s", exc)
        app.state.telegram = None

    # Start LLM filter (best-effort)
    try:
        from prophet.core.llm_filter import llm_filter
        await llm_filter.start()
        app.state.llm_filter = llm_filter
        if llm_filter.enabled:
            logger.info("LLM pre-trade filter started.")
    except Exception as exc:
        logger.warning("LLM filter could not start: %s", exc)

    # Start WebSocket price listener (best-effort; non-fatal if it fails)
    try:
        from prophet.core.ws_listener import PolymarketWSListener

        ws_listener = PolymarketWSListener()
        await ws_listener.start()
        app.state.ws_listener = ws_listener
        logger.info("PolymarketWSListener started.")
    except Exception as exc:
        logger.warning(
            "PolymarketWSListener could not start (non-fatal — API still available): %s", exc
        )
        app.state.ws_listener = None

    logger.info("Prophet Engine ready.")

    yield  # ---- application is running ----

    # ---- SHUTDOWN ----
    logger.info("Prophet Engine shutting down…")

    # Stop Telegram notifier
    tg = getattr(app.state, "telegram", None)
    if tg is not None:
        try:
            if tg.enabled:
                await tg._send("🛑 <b>Prophet Engine shutting down</b>")
            await tg.stop()
        except Exception:
            pass

    # Stop LLM filter
    llm = getattr(app.state, "llm_filter", None)
    if llm is not None:
        try:
            await llm.stop()
        except Exception:
            pass

    ws_listener = getattr(app.state, "ws_listener", None)
    if ws_listener is not None:
        try:
            await ws_listener.stop()
            logger.info("PolymarketWSListener stopped.")
        except Exception as exc:
            logger.warning("Error stopping PolymarketWSListener: %s", exc)

    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        try:
            await scheduler.stop()
            logger.info("Scheduler stopped.")
        except Exception as exc:
            logger.warning("Error stopping scheduler: %s", exc)

    # Close persistent DB session
    db_session = getattr(app.state, "db_session", None)
    if db_session is not None:
        try:
            await db_session.close()
        except Exception:
            pass

    # Close HTTP clients
    for attr in ("gamma_client", "clob_client"):
        client = getattr(app.state, attr, None)
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    from prophet.db.database import close_db

    await close_db()
    logger.info("Prophet Engine shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Prophet Engine",
        version="0.1.0",
        description=(
            "REST API for the Prophet Polymarket trading engine. "
            "All endpoints (except /health) require a Bearer token."
        ),
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — must be added before BearerTokenMiddleware
    application.add_middleware(CORSMiddleware, **CORS_KWARGS)

    # Bearer token auth
    application.add_middleware(BearerTokenMiddleware)

    # Include routers
    _PREFIX = "/api/v1"
    application.include_router(system.router, prefix=_PREFIX)
    application.include_router(markets.router, prefix=_PREFIX)
    application.include_router(strategies.router, prefix=_PREFIX)
    application.include_router(signals.router, prefix=_PREFIX)
    application.include_router(positions.router, prefix=_PREFIX)
    application.include_router(performance.router, prefix=_PREFIX)
    application.include_router(config.router, prefix=_PREFIX)
    application.include_router(data.router, prefix=_PREFIX)

    # Global exception handler
    @application.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )

    return application


# Module-level app instance (used by uvicorn)
app = create_app()
