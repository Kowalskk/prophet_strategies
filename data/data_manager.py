"""
PROPHET STRATEGIES
Data Manager — orchestrates data download, parsing, and SQLite caching
"""
from __future__ import annotations
import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from data.dune_client import DuneClient
from data.market_resolver import MarketParser, parse_resolution
from data.price_fetcher import PriceFetcher
from models.market import Market, Outcome, CryptoAsset

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Dune SQL queries
# ------------------------------------------------------------------

QUERY_TRADES = """
SELECT
    t.block_time,
    t.condition_id,
    t.question,
    t.event_market_name,
    t.token_outcome,
    t.token_outcome_name,
    t.price,
    t.amount,
    t.shares,
    t.fee,
    t.neg_risk,
    t.maker,
    t.taker
FROM polymarket_polygon.market_trades t
WHERE t.block_time >= CAST('{start_date}' AS TIMESTAMP)
  AND t.block_time <= CAST('{end_date}' AS TIMESTAMP)
  -- Must be BTC, ETH or SOL
  AND (
    LOWER(t.question) LIKE '%bitcoin%' OR LOWER(t.question) LIKE '%btc%'
    OR LOWER(t.question) LIKE '%ethereum%' OR LOWER(t.question) LIKE '%eth%'
    OR LOWER(t.question) LIKE '%solana%' OR LOWER(t.question) LIKE '%sol%'
  )
  -- Must contain "above" — the only direction we backtest
  AND LOWER(t.question) LIKE '%above%'
  -- Must contain "on [date]" — weekly resolution markets only
  AND LOWER(t.question) LIKE '% on %'
  -- Exclude anything with "hit" — different market type
  AND LOWER(t.question) NOT LIKE '%hit%'
  -- Exclude "X or Y first" comparison markets
  AND LOWER(t.question) NOT LIKE '%or%first%'
  -- Exclude year-end markets "in 202X"
  AND LOWER(t.question) NOT LIKE '%in 202%'
  -- Exclude "again", "ever", "today", "by sunday/monday..." day-of-week markets
  AND LOWER(t.question) NOT LIKE '%again%'
  AND LOWER(t.question) NOT LIKE '%ever%'
  AND LOWER(t.question) NOT LIKE '%today%'
  AND LOWER(t.question) NOT LIKE '%sunday%'
  AND LOWER(t.question) NOT LIKE '%monday%'
  AND LOWER(t.question) NOT LIKE '%tuesday%'
  AND LOWER(t.question) NOT LIKE '%wednesday%'
  AND LOWER(t.question) NOT LIKE '%thursday%'
  AND LOWER(t.question) NOT LIKE '%friday%'
  AND LOWER(t.question) NOT LIKE '%saturday%'
ORDER BY t.block_time ASC
"""

QUERY_RESOLUTIONS = """
SELECT
    r.conditionId as condition_id,
    r.evt_block_time as resolution_time,
    r.payoutNumerators
FROM polymarket_polygon.ctf_evt_conditionresolution r
WHERE r.evt_block_time >= CAST('{start_date}' AS TIMESTAMP)
  AND r.evt_block_time <= CAST('{end_date}' AS TIMESTAMP)
"""

# ------------------------------------------------------------------
# SQLite schema
# ------------------------------------------------------------------

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS market_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    block_time TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    question TEXT,
    event_market_name TEXT,
    token_outcome TEXT,
    token_outcome_name TEXT,
    price REAL,
    amount REAL,
    shares REAL,
    fee REAL,
    neg_risk INTEGER,
    maker TEXT,
    taker TEXT
)
"""

CREATE_RESOLUTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS market_resolutions (
    condition_id TEXT PRIMARY KEY,
    resolution_time TEXT,
    resolved_outcome TEXT,
    payout_numerators TEXT
)
"""

CREATE_MARKETS_TABLE = """
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT,
    event_market_name TEXT,
    crypto TEXT,
    threshold REAL,
    direction TEXT,
    resolution_date TEXT,
    period_type TEXT,
    resolved_outcome TEXT,
    resolution_time TEXT,
    first_trade_time TEXT,
    last_trade_time TEXT,
    total_volume_usd REAL,
    trade_count INTEGER,
    neg_risk INTEGER
)
"""

CREATE_PRICES_TABLE = """
CREATE TABLE IF NOT EXISTS crypto_prices (
    crypto TEXT NOT NULL,
    price_date TEXT NOT NULL,
    close_price REAL NOT NULL,
    PRIMARY KEY (crypto, price_date)
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_condition ON market_trades(condition_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_time ON market_trades(block_time)",
    "CREATE INDEX IF NOT EXISTS idx_trades_price ON market_trades(price)",
    "CREATE INDEX IF NOT EXISTS idx_markets_crypto ON markets(crypto)",
    "CREATE INDEX IF NOT EXISTS idx_markets_resolution_date ON markets(resolution_date)",
    "CREATE INDEX IF NOT EXISTS idx_markets_outcome ON markets(resolved_outcome)",
]


class DataManager:
    """Manages all data: downloads from Dune, parses markets, stores in SQLite."""

    def __init__(self, db_path: str = "data/prophet.db", dune_api_key: Optional[str] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        
        # In-memory caches for ultra-fast backtesting
        self.trade_cache = {}    # cid -> {YES: df, NO: df, ALL: df}
        self.market_cache = {}   # crypto -> list of Market objects
        self.cached_crypto = None # Tracks which crypto's trades are currently preloaded

        try:
            self.dune = DuneClient(api_key=dune_api_key)
        except ValueError:
            self.dune = None  # OK — backtest reads do not need Dune
        self.parser = MarketParser()
        self.price_fetcher = PriceFetcher()

    def _init_schema(self):
        cursor = self.conn.cursor()
        for stmt in [CREATE_TRADES_TABLE, CREATE_RESOLUTIONS_TABLE, CREATE_MARKETS_TABLE, CREATE_PRICES_TABLE]:
            cursor.execute(stmt)
        for idx in INDEXES:
            cursor.execute(idx)
        self.conn.commit()
        logger.info(f"SQLite schema initialized at {self.db_path}")

    # ------------------------------------------------------------------
    # Download from Dune
    # ------------------------------------------------------------------

    def fetch_trades(self, start_date: str, end_date: str, batch_months: int = 2) -> int:
        """
        Download market trades from Dune and store in SQLite.
        Batches by month to avoid hitting the 250K row limit.
        Returns total rows downloaded.
        """
        from datetime import date as date_cls
        from dateutil.relativedelta import relativedelta

        start = date_cls.fromisoformat(start_date)
        end = date_cls.fromisoformat(end_date) if end_date else date_cls.today()

        total_rows = 0
        current = start

        while current < end:
            batch_end = min(current + relativedelta(months=batch_months), end)
            logger.info(f"Fetching trades: {current} → {batch_end}")

            sql = QUERY_TRADES.format(
                start_date=current.isoformat(),
                end_date=batch_end.isoformat()
            )

            rows = self.dune.run_sql_and_collect(sql, query_name=f"prophet_trades_{current}")
            if rows:
                self._insert_trades(rows)
                total_rows += len(rows)
                logger.info(f"Inserted {len(rows):,} trades for {current} → {batch_end}")

            current = batch_end

        return total_rows

    def fetch_resolutions(self, start_date: str, end_date: str) -> int:
        """Download market resolutions from Dune."""
        end = end_date or date.today().isoformat()
        sql = QUERY_RESOLUTIONS.format(start_date=start_date, end_date=end)
        rows = self.dune.run_sql_and_collect(sql, query_name="prophet_resolutions")
        if rows:
            self._insert_resolutions(rows)
        return len(rows)

    # ------------------------------------------------------------------
    # Insert into SQLite
    # ------------------------------------------------------------------

    def _insert_trades(self, rows: list[dict]):
        cursor = self.conn.cursor()
        # Use INSERT OR IGNORE to avoid duplicates on re-run
        cursor.executemany("""
            INSERT OR IGNORE INTO market_trades
            (block_time, condition_id, question, event_market_name,
             token_outcome, token_outcome_name, price, amount, shares,
             fee, neg_risk, maker, taker)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                str(r.get("block_time", "")),
                str(r.get("condition_id", "")),
                str(r.get("question", "")),
                str(r.get("event_market_name", "")),
                str(r.get("token_outcome", "")),
                str(r.get("token_outcome_name", "")),
                float(r.get("price", 0)),
                float(r.get("amount", 0)),
                float(r.get("shares", 0)),
                float(r.get("fee", 0)),
                1 if str(r.get("neg_risk", "false")).lower() == "true" else 0,
                str(r.get("maker", "")),
                str(r.get("taker", "")),
            )
            for r in rows
        ])
        self.conn.commit()

    def _insert_resolutions(self, rows: list[dict]):
        cursor = self.conn.cursor()
        for r in rows:
            payout = r.get("payoutNumerators", [])
            if isinstance(payout, str):
                import json
                payout = json.loads(payout)
            outcome = parse_resolution(payout)
            cursor.execute("""
                INSERT OR REPLACE INTO market_resolutions
                (condition_id, resolution_time, resolved_outcome, payout_numerators)
                VALUES (?, ?, ?, ?)
            """, (
                str(r.get("condition_id", "")),
                str(r.get("resolution_time", "")),
                outcome.value,
                str(payout),
            ))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Build markets table
    # ------------------------------------------------------------------

    def build_markets(self) -> int:
        """
        Parse all unique markets from trades + join resolutions.
        Populates the markets table.
        Returns number of markets processed.
        """
        logger.info("Building markets table from trades...")

        df_trades = pd.read_sql("""
            SELECT condition_id, question, event_market_name,
                   MIN(block_time) as first_trade_time,
                   MAX(block_time) as last_trade_time,
                   SUM(amount) as total_volume_usd,
                   COUNT(*) as trade_count,
                   MAX(neg_risk) as neg_risk
            FROM market_trades
            GROUP BY condition_id, question, event_market_name
        """, self.conn)

        df_resolutions = pd.read_sql(
            "SELECT condition_id, resolution_time, resolved_outcome FROM market_resolutions",
            self.conn
        )

        df = df_trades.merge(df_resolutions, on="condition_id", how="left")
        df["resolved_outcome"] = df["resolved_outcome"].fillna("UNKNOWN")

        count = 0
        cursor = self.conn.cursor()

        for _, row in df.iterrows():
            market = Market(
                condition_id=row["condition_id"],
                question=row["question"],
                event_market_name=row.get("event_market_name", ""),
                total_volume_usd=float(row.get("total_volume_usd", 0)),
                trade_count=int(row.get("trade_count", 0)),
                neg_risk=bool(row.get("neg_risk", 0)),
            )

            # Parse the question
            self.parser.parse(market)

            # Set resolution
            if row.get("resolved_outcome"):
                market.resolved_outcome = Outcome(row["resolved_outcome"])
            if row.get("resolution_time"):
                try:
                    market.resolution_time = datetime.fromisoformat(str(row["resolution_time"]))
                except Exception:
                    pass

            # Insert into markets table
            cursor.execute("""
                INSERT OR REPLACE INTO markets
                (condition_id, question, event_market_name, crypto, threshold,
                 direction, resolution_date, period_type, resolved_outcome,
                 resolution_time, first_trade_time, last_trade_time,
                 total_volume_usd, trade_count, neg_risk)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market.condition_id,
                market.question,
                market.event_market_name,
                market.crypto.value if market.crypto else None,
                market.threshold,
                market.direction.value if market.direction else None,
                market.resolution_date.isoformat() if market.resolution_date else None,
                market.period_type.value if market.period_type else None,
                market.resolved_outcome.value,
                market.resolution_time.isoformat() if market.resolution_time else None,
                str(row.get("first_trade_time", "")),
                str(row.get("last_trade_time", "")),
                float(row.get("total_volume_usd", 0)),
                int(row.get("trade_count", 0)),
                1 if market.neg_risk else 0,
            ))
            count += 1

        self.conn.commit()
        logger.info(f"Built {count:,} markets in DB")
        return count

    # ------------------------------------------------------------------
    # Fetch crypto prices
    # ------------------------------------------------------------------

    def fetch_prices(self, cryptos: list[str], start_date: str, end_date: str):
        """Download and store historical prices for given cryptos."""
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date) if end_date else date.today()

        cursor = self.conn.cursor()
        for crypto in cryptos:
            prices = self.price_fetcher.fetch_daily_prices(crypto, start, end)
            for price_date, price in prices.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO crypto_prices (crypto, price_date, close_price)
                    VALUES (?, ?, ?)
                """, (crypto, price_date.isoformat(), price))
        self.conn.commit()
        logger.info(f"Stored prices for {cryptos}")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> dict:
        """Run validation checks. Returns dict of stats."""
        cursor = self.conn.cursor()
        stats = {}

        stats["total_trades"] = cursor.execute("SELECT COUNT(*) FROM market_trades").fetchone()[0]
        stats["total_markets"] = cursor.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
        stats["parsed_markets"] = cursor.execute(
            "SELECT COUNT(*) FROM markets WHERE crypto IS NOT NULL AND threshold IS NOT NULL"
        ).fetchone()[0]
        stats["resolved_markets"] = cursor.execute(
            "SELECT COUNT(*) FROM markets WHERE resolved_outcome IN ('YES', 'NO')"
        ).fetchone()[0]

        # Per-crypto breakdown
        for crypto in ["BTC", "ETH", "SOL"]:
            stats[f"{crypto}_markets"] = cursor.execute(
                "SELECT COUNT(*) FROM markets WHERE crypto = ?", (crypto,)
            ).fetchone()[0]
            stats[f"{crypto}_resolved"] = cursor.execute(
                "SELECT COUNT(*) FROM markets WHERE crypto = ? AND resolved_outcome IN ('YES', 'NO')", (crypto,)
            ).fetchone()[0]

        # Date range
        row = cursor.execute(
            "SELECT MIN(block_time), MAX(block_time) FROM market_trades"
        ).fetchone()
        stats["earliest_trade"] = row[0]
        stats["latest_trade"] = row[1]

        stats["price_records"] = cursor.execute("SELECT COUNT(*) FROM crypto_prices").fetchone()[0]

        return stats

    # ------------------------------------------------------------------
    # Read helpers for backtest engine
    # ------------------------------------------------------------------

    def preload_trades(self, crypto: Optional[str] = None):
        """Load and pre-filter trades into memory. Shards by crypto to save RAM."""
        if crypto and self.cached_crypto == crypto:
            return # Already loaded
            
        if self.cached_crypto is not None:
            self.clear_trade_cache()

        logger.info(f"Preloading and pre-filtering {crypto or 'ALL'} trades into memory...")
        
        if crypto:
            query = """
                SELECT t.block_time, t.condition_id, t.token_outcome, t.price, t.amount, t.shares, t.fee
                FROM market_trades t
                JOIN markets m ON t.condition_id = m.condition_id
                WHERE m.crypto = ?
            """
            df_all = pd.read_sql(query, self.conn, params=[crypto])
        else:
            query = """
                SELECT block_time, condition_id, token_outcome, price, amount, shares, fee
                FROM market_trades
            """
            df_all = pd.read_sql(query, self.conn)
        
        if df_all.empty:
            logger.warning(f"No trades found for {crypto or 'ALL'}")
            return
        
        # 1. Faster datetime conversion (once)
        df_all["block_time"] = pd.to_datetime(df_all["block_time"])
        
        # 2. Case-normalization for outcome (once)
        df_all["token_outcome"] = df_all["token_outcome"].str.upper()
        
        # 3. Group by condition_id and pre-split YES/NO
        for cond_id, group in df_all.groupby("condition_id"):
            cid = str(cond_id)
            group = group.drop(columns=["condition_id"]).sort_values("block_time")
            
            # Sub-split by outcome for instant fill simulation
            self.trade_cache[cid] = {
                "YES": group[group["token_outcome"] == "YES"].copy(),
                "NO":  group[group["token_outcome"] == "NO"].copy(),
                "ALL": group
            }
        self.cached_crypto = crypto
        logger.info(f"Preloaded and bucketed {len(self.trade_cache):,} market trade-groups for {crypto or 'ALL'}")

    def clear_trade_cache(self):
        """Free memory by clearing the trade cache."""
        self.trade_cache.clear()
        self.cached_crypto = None
        import gc
        gc.collect()

    def get_trades_for_market(self, condition_id: str, outcome: Optional[str] = None) -> pd.DataFrame:
        """Return trades for a market, using pre-filtered cache if available."""
        if condition_id in self.trade_cache:
            bucket = self.trade_cache[condition_id]
            if outcome:
                return bucket.get(outcome.upper(), pd.DataFrame())
            return bucket["ALL"]
            
        # Fallback to slow SQL
        query = "SELECT block_time, token_outcome, price, amount, shares, fee FROM market_trades WHERE condition_id = ?"
        params = [condition_id]
        if outcome:
            query += " AND UPPER(token_outcome) = ?"
            params.append(outcome.upper())
            
        df = pd.read_sql(query, self.conn, params=params)
        df["block_time"] = pd.to_datetime(df["block_time"])
        return df.sort_values("block_time")

    def get_markets(
        self,
        crypto: Optional[str] = None,
        resolved_only: bool = True,
        min_trades: int = 5,
    ) -> pd.DataFrame:
        """Return markets matching filters."""
        query = """
            SELECT * FROM markets
            WHERE trade_count >= ?
              AND crypto IS NOT NULL
              AND threshold IS NOT NULL
              AND resolution_date IS NOT NULL
        """
        params = [min_trades]

        if crypto:
            query += " AND crypto = ?"
            params.append(crypto)

        if resolved_only:
            query += " AND resolved_outcome IN ('YES', 'NO')"

        return pd.read_sql(query, self.conn, params=params)

    def preload_markets(self, resolved_only: bool = True, min_trades: int = 5):
        """Pre-convert markets to Market objects for ultra-fast iteration."""
        from backtest.engine import BacktestEngine
        # We need a dummy engine to use its conversion logic
        dummy_engine = BacktestEngine(self)
        
        logger.info(f"Pre-converting markets into memory objects...")
        df = self.get_markets(resolved_only=resolved_only, min_trades=min_trades)
        
        # Cache for ALL cryptos
        all_markets = []
        for _, row in df.iterrows():
            market = dummy_engine._row_to_market(row)
            all_markets.append(market)
            
        self.market_cache[None] = all_markets
        
        # Also cache per crypto
        for crypto in ["BTC", "ETH", "SOL"]:
            self.market_cache[crypto] = [m for m in all_markets if m.crypto.value == crypto]
            
        logger.info(f"Preloaded {len(all_markets):,} Market objects into RAM")

    def get_crypto_price(self, crypto: str, price_date: str) -> Optional[float]:
        """Get stored price for crypto on date."""
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT close_price FROM crypto_prices WHERE crypto = ? AND price_date = ?",
            (crypto, price_date)
        ).fetchone()
        return float(row[0]) if row else None

    def close(self):
        self.conn.close()


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main():
    import click
    import yaml
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    @click.command()
    @click.option("--config", default="config/config.yaml", help="Path to config.yaml")
    @click.option("--fetch", is_flag=True, help="Download data from Dune")
    @click.option("--build-markets", is_flag=True, help="Build markets table from trades")
    @click.option("--fetch-prices", is_flag=True, help="Download crypto prices")
    @click.option("--validate", is_flag=True, help="Validate data and show stats")
    def cli(config, fetch, build_markets, fetch_prices, validate):
        with open(config) as f:
            cfg = yaml.safe_load(f)

        data_cfg = cfg["data"]
        dm = DataManager(db_path=data_cfg["cache_db"])

        start = data_cfg["start_date"]
        end = data_cfg.get("end_date") or date.today().isoformat()

        if fetch:
            logger.info("=== Fetching trades from Dune ===")
            n = dm.fetch_trades(start, end)
            logger.info(f"Downloaded {n:,} trade rows")

            logger.info("=== Fetching resolutions from Dune ===")
            n = dm.fetch_resolutions(start, end)
            logger.info(f"Downloaded {n:,} resolution rows")

        if build_markets or fetch:
            logger.info("=== Building markets table ===")
            n = dm.build_markets()
            logger.info(f"Built {n:,} markets")

        if fetch_prices or fetch:
            logger.info("=== Fetching crypto prices ===")
            dm.fetch_prices(data_cfg["cryptos"], start, end)

        if validate or fetch:
            logger.info("=== Validation ===")
            stats = dm.validate()
            for k, v in stats.items():
                logger.info(f"  {k}: {v}")
            
            parse_rate = stats.get("parsed_markets", 0) / max(stats.get("total_markets", 1), 1) * 100
            resolve_rate = stats.get("resolved_markets", 0) / max(stats.get("total_markets", 1), 1) * 100
            logger.info(f"  Parse rate: {parse_rate:.1f}%")
            logger.info(f"  Resolve rate: {resolve_rate:.1f}%")

            if stats.get("total_trades", 0) > 0:
                logger.info("✅ Data validated OK")
            else:
                logger.error("❌ No trades found — check Dune connection and API key")

        dm.close()

    cli()


if __name__ == "__main__":
    main()
