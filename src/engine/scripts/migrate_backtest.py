"""
Migration script: import backtest SQLite results into PostgreSQL.

Usage::

    cd engine
    python -m scripts.migrate_backtest

The script is idempotent — it uses condition_id to detect existing markets.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locate the SQLite database
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]  # …/Polymarket Strategies/

SQLITE_CANDIDATES = [
    BASE_DIR / "data" / "backtest.db",
    BASE_DIR / "data" / "backtest.sqlite",
    BASE_DIR / "output" / "backtest.db",
    BASE_DIR / "output" / "backtest.sqlite",
    BASE_DIR / "backtest" / "backtest.db",
    BASE_DIR / "backtest" / "backtest.sqlite",
    BASE_DIR / "backtest.db",
    BASE_DIR / "backtest.sqlite",
]


def _find_sqlite() -> Path | None:
    """Return the first existing SQLite candidate path, or None."""
    for p in SQLITE_CANDIDATES:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(value: str | int | float | None) -> datetime | None:
    """Convert SQLite timestamp value to a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------


async def migrate(sqlite_path: Path) -> None:
    """Run the full migration from *sqlite_path* into the configured PostgreSQL."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from prophet.config import settings
    from prophet.db.database import init_db
    from prophet.db.models import Market, PaperOrder, Position
    from prophet.db.repositories import MarketRepository, PaperOrderRepository, PositionRepository

    logger.info("SQLite source: %s", sqlite_path)
    logger.info("PostgreSQL target: %s", settings.database_url.split("@")[-1])

    # ----------------------------------------------------------------
    # Connect SQLite (synchronous)
    # ----------------------------------------------------------------
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ----------------------------------------------------------------
    # Inspect available tables
    # ----------------------------------------------------------------
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cur.fetchall()}
    logger.info("SQLite tables found: %s", sorted(tables))

    # ----------------------------------------------------------------
    # Connect PostgreSQL (async)
    # ----------------------------------------------------------------
    engine = create_async_engine(settings.database_url, future=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    await init_db(create_tables=False)

    markets_migrated = 0
    trades_migrated = 0
    errors = 0

    async with async_session() as db:
        # ------------------------------------------------------------
        # 1. Migrate markets
        # ------------------------------------------------------------
        market_id_map: dict[str, int] = {}  # condition_id → PG id

        if "markets" in tables:
            cur.execute("SELECT * FROM markets")
            rows = cur.fetchall()
            logger.info("Migrating %d market(s) …", len(rows))

            for row in rows:
                try:
                    condition_id = str(
                        row["condition_id"]
                        if "condition_id" in row.keys()
                        else row["id"]
                    )

                    # Check idempotency
                    existing = await MarketRepository.get_by_condition_id(db, condition_id)
                    if existing:
                        market_id_map[condition_id] = existing.id
                        continue

                    status = str(row["status"]) if "status" in row.keys() else "resolved"
                    resolved_outcome = (
                        str(row["outcome"]) if "outcome" in row.keys() else None
                    ) or (
                        str(row["resolved_outcome"]) if "resolved_outcome" in row.keys() else None
                    )

                    data: dict = {
                        "condition_id": condition_id,
                        "question": str(row["question"]) if "question" in row.keys() else f"Backtest market {condition_id}",
                        "crypto": str(row["crypto"]) if "crypto" in row.keys() else "BTC",
                        "threshold": float(row["threshold"]) if "threshold" in row.keys() and row["threshold"] is not None else None,
                        "direction": str(row["direction"]) if "direction" in row.keys() else None,
                        "token_id_yes": str(row["token_id_yes"]) if "token_id_yes" in row.keys() else f"{condition_id}_yes",
                        "token_id_no": str(row["token_id_no"]) if "token_id_no" in row.keys() else f"{condition_id}_no",
                        "status": "resolved" if resolved_outcome else status,
                        "resolved_outcome": resolved_outcome,
                    }

                    if "resolution_date" in row.keys():
                        data["resolution_time"] = _to_datetime(row["resolution_date"])

                    market = await MarketRepository.upsert(db, data)
                    market_id_map[condition_id] = market.id
                    markets_migrated += 1

                except Exception as exc:
                    logger.warning("Market row error: %s — %s", dict(row), exc)
                    errors += 1

            await db.commit()
            logger.info("Markets committed: %d new, %d errors", markets_migrated, errors)

        # ------------------------------------------------------------
        # 2. Migrate trades → PaperOrder + Position
        # ------------------------------------------------------------
        trade_table = next(
            (t for t in ("trades", "backtest_trades", "results", "paper_trades") if t in tables),
            None,
        )

        if trade_table:
            cur.execute(f"SELECT * FROM {trade_table}")
            rows = cur.fetchall()
            logger.info("Migrating %d trade(s) from '%s' …", len(rows), trade_table)

            for row in rows:
                try:
                    cols = row.keys()

                    # Resolve market FK
                    cid = str(row["condition_id"]) if "condition_id" in cols else None
                    market_pg_id = market_id_map.get(cid) if cid else None

                    # If market not in map, try to find by any reference
                    if market_pg_id is None and "market_id" in cols:
                        raw_mid = str(row["market_id"])
                        market_pg_id = market_id_map.get(raw_mid)

                    if market_pg_id is None:
                        # Create a stub market for orphaned trade
                        stub_cid = cid or f"bt_stub_{trades_migrated}"
                        existing = await MarketRepository.get_by_condition_id(db, stub_cid)
                        if existing:
                            market_pg_id = existing.id
                        else:
                            stub = await MarketRepository.upsert(db, {
                                "condition_id": stub_cid,
                                "question": f"[Backtest] {stub_cid}",
                                "crypto": str(row["crypto"]) if "crypto" in cols else "BTC",
                                "token_id_yes": f"{stub_cid}_yes",
                                "token_id_no": f"{stub_cid}_no",
                                "status": "resolved",
                            })
                            market_pg_id = stub.id

                    strategy = str(row["strategy"]) if "strategy" in cols else "backtest_import"
                    side = str(row["side"]).upper() if "side" in cols else "YES"
                    entry_price = float(row["entry_price"]) if "entry_price" in cols else 0.50
                    exit_price = float(row["exit_price"]) if "exit_price" in cols else 0.0
                    size_usd = float(row["size_usd"] if "size_usd" in cols else row["amount"]) if ("size_usd" in cols or "amount" in cols) else 50.0
                    
                    shares = size_usd / entry_price if entry_price > 0 else 0.0

                    # PnL
                    if "pnl" in cols and row["pnl"] is not None:
                        net_pnl = float(row["pnl"])
                        gross_pnl = net_pnl
                        fees = 0.0
                    elif exit_price > 0:
                        gross_pnl = (exit_price - entry_price) * shares
                        fees = shares * exit_price * 0.02
                        net_pnl = gross_pnl - fees
                    else:
                        gross_pnl = 0.0
                        fees = 0.0
                        net_pnl = 0.0

                    placed_at = _to_datetime(row["created_at"]) if "created_at" in cols else _utcnow()
                    filled_at = _to_datetime(row["filled_at"]) if "filled_at" in cols else placed_at
                    closed_at = _to_datetime(row["closed_at"]) if "closed_at" in cols else filled_at

                    # PaperOrder (filled)
                    po = PaperOrder(
                        market_id=market_pg_id,
                        strategy=strategy,
                        side=side,
                        order_type="limit",
                        target_price=entry_price,
                        size_usd=size_usd,
                        status="filled",
                        placed_at=placed_at or _utcnow(),
                        filled_at=filled_at,
                        fill_price=entry_price,
                        fill_size_usd=size_usd,
                    )
                    await PaperOrderRepository.create(db, po)

                    # Position (closed)
                    pos = Position(
                        market_id=market_pg_id,
                        strategy=strategy,
                        side=side,
                        entry_price=entry_price,
                        size_usd=size_usd,
                        shares=shares,
                        status="closed",
                        opened_at=placed_at or _utcnow(),
                        closed_at=closed_at,
                        exit_price=exit_price if exit_price > 0 else None,
                        exit_reason="backtest_import",
                        gross_pnl=round(gross_pnl, 6),
                        fees=round(fees, 6),
                        net_pnl=round(net_pnl, 6),
                    )
                    await PositionRepository.create(db, pos)
                    trades_migrated += 1

                except Exception as exc:
                    logger.warning("Trade row error: %s — %s", exc, "")
                    errors += 1

            await db.commit()
            logger.info("Trades committed: %d new, %d errors", trades_migrated, errors)
        else:
            logger.warning("No trade/results table found in SQLite. Supported table names: trades, backtest_trades, results, paper_trades")

    conn.close()
    await engine.dispose()

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    print("\n" + "=" * 50)
    print("MIGRATION SUMMARY")
    print("=" * 50)
    print(f"  Markets migrated : {markets_migrated}")
    print(f"  Trades migrated  : {trades_migrated}")
    print(f"  Errors           : {errors}")
    print("=" * 50)


def main() -> None:
    sqlite_path = _find_sqlite()
    if sqlite_path is None:
        print(
            "\nWARNING: SQLite database not found.\n"
            "Searched locations:\n" +
            "\n".join(f"  - {p}" for p in SQLITE_CANDIDATES) +
            "\n\nPlease place your backtest.db file in one of the above paths and re-run."
        )
        sys.exit(0)

    asyncio.run(migrate(sqlite_path))


if __name__ == "__main__":
    main()
