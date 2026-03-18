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
        from prophet.polymarket.orderbook import OrderBookService
        from prophet.polymarket.price_feeds import PriceFeedService

        # Build shared HTTP clients
        gamma_client = GammaClient()
        await gamma_client.start()

        clob_client = PolymarketClient()
        await clob_client.start()

        # Create a persistent session for scheduler tasks.
        # We intentionally do NOT use the get_session() context manager here —
        # that would close the session immediately after start().
        # Each component calls commit() at the end of its own job methods.
        from prophet.db.database import get_session_factory
        _db_session = get_session_factory()()

        scanner = MarketScanner(gamma_client=gamma_client, db_session=_db_session)
        ob_service = OrderBookService(clob_client=clob_client, db_session=_db_session)
        price_service = PriceFeedService(db_session=_db_session)
        data_collector = DataCollector(
            clob_client=clob_client,
            db_session=_db_session,
            redis_client=None,
        )
        risk_mgr = RiskManager(db_session=_db_session)
        signal_gen = SignalGenerator(
            clob_client=clob_client,
            db_session=_db_session,
            risk_manager=risk_mgr,
        )
        order_mgr = OrderManager(
            clob_client=clob_client,
            db_session=_db_session,
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
        app.state.db_session = _db_session
        app.state.gamma_client = gamma_client
        app.state.clob_client = clob_client
        logger.info("Scheduler started.")
    except Exception as exc:
        logger.warning(
            "Scheduler could not start (non-fatal — API still available): %s", exc
        )
        app.state.scheduler = None

    logger.info("Prophet Engine ready.")

    yield  # ---- application is running ----

    # ---- SHUTDOWN ----
    logger.info("Prophet Engine shutting down…")

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
