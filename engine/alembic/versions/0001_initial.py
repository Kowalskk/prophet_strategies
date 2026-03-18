"""Initial migration — create all 9 Prophet tables.

Revision ID: 0001_initial
Revises: (none — this is the first migration)
Create Date: 2026-03-18

Tables created:
  1. markets
  2. orderbook_snapshots
  3. observed_trades
  4. signals
  5. paper_orders
  6. positions
  7. price_snapshots
  8. system_state
  9. strategy_configs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Alembic revision identifiers
# ---------------------------------------------------------------------------

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# Upgrade — create all tables
# ---------------------------------------------------------------------------


def upgrade() -> None:

    # -----------------------------------------------------------------------
    # 1. markets
    # -----------------------------------------------------------------------
    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("condition_id", sa.String(length=255), nullable=False,
                  comment="Polymarket condition ID (hex string)."),
        sa.Column("question", sa.String(length=1024), nullable=False,
                  comment="Full question text as displayed on Polymarket."),
        sa.Column("crypto", sa.String(length=10), nullable=False,
                  comment="Crypto symbol: BTC, ETH, or SOL."),
        sa.Column("threshold", sa.Float(), nullable=True,
                  comment="Price threshold parsed from question."),
        sa.Column("direction", sa.String(length=10), nullable=True,
                  comment="ABOVE or BELOW."),
        sa.Column("resolution_date", sa.Date(), nullable=True,
                  comment="Date (UTC) when the market resolves."),
        sa.Column("token_id_yes", sa.String(length=255), nullable=False,
                  comment="CLOB token ID for the YES outcome."),
        sa.Column("token_id_no", sa.String(length=255), nullable=False,
                  comment="CLOB token ID for the NO outcome."),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="active",
                  comment="Market lifecycle: active | resolved | expired."),
        sa.Column("resolved_outcome", sa.String(length=10), nullable=True,
                  comment="Final outcome: YES or NO."),
        sa.Column("resolution_time", sa.DateTime(timezone=True), nullable=True,
                  comment="Actual UTC timestamp when the market resolved."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("condition_id", name="uq_markets_condition_id"),
    )
    op.create_index("ix_markets_condition_id", "markets", ["condition_id"], unique=True)
    op.create_index("ix_markets_crypto_status", "markets", ["crypto", "status"])
    op.create_index("ix_markets_resolution_date", "markets", ["resolution_date"])
    op.create_index("ix_markets_status", "markets", ["status"])

    # -----------------------------------------------------------------------
    # 2. orderbook_snapshots
    # -----------------------------------------------------------------------
    op.create_table(
        "orderbook_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.String(length=255), nullable=False,
                  comment="CLOB token ID (YES or NO side)."),
        sa.Column("side", sa.String(length=5), nullable=False,
                  comment="'yes' or 'no'."),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_bid", sa.Float(), nullable=True),
        sa.Column("best_ask", sa.Float(), nullable=True),
        sa.Column("bid_depth_10pct", sa.Float(), nullable=False, server_default="0",
                  comment="Total USD available within 10% of the best bid."),
        sa.Column("ask_depth_10pct", sa.Float(), nullable=False, server_default="0",
                  comment="Total USD available within 10% of the best ask."),
        sa.Column("spread_pct", sa.Float(), nullable=True,
                  comment="(best_ask - best_bid) / best_ask * 100."),
        sa.Column("raw_book", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}",
                  comment="Full bid/ask arrays as returned by the CLOB API."),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_obs_market_token_ts", "orderbook_snapshots",
                    ["market_id", "token_id", "timestamp"])
    op.create_index("ix_obs_timestamp", "orderbook_snapshots", ["timestamp"])
    op.create_index("ix_orderbook_snapshots_market_id", "orderbook_snapshots", ["market_id"])

    # -----------------------------------------------------------------------
    # 3. observed_trades
    # -----------------------------------------------------------------------
    op.create_table(
        "observed_trades",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.String(length=255), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False, comment="YES or NO."),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("size_usd", sa.Float(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("maker", sa.String(length=255), nullable=False, server_default="",
                  comment="Maker wallet address."),
        sa.Column("taker", sa.String(length=255), nullable=False, server_default="",
                  comment="Taker wallet address."),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ot_market_token_ts", "observed_trades",
                    ["market_id", "token_id", "timestamp"])
    op.create_index("ix_ot_timestamp", "observed_trades", ["timestamp"])
    op.create_index("ix_observed_trades_market_id", "observed_trades", ["market_id"])

    # -----------------------------------------------------------------------
    # 4. signals
    # -----------------------------------------------------------------------
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False,
                  comment="Strategy name: volatility_spread | stink_bid | liquidity_sniper."),
        sa.Column("side", sa.String(length=5), nullable=False, comment="YES or NO."),
        sa.Column("target_price", sa.Float(), nullable=False),
        sa.Column("size_usd", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0",
                  comment="Strategy confidence score in [0, 1]."),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}",
                  comment="Strategy parameters snapshot at signal creation."),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending",
                  comment="pending | executed | expired | rejected."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_market_strategy", "signals", ["market_id", "strategy"])
    op.create_index("ix_signals_status", "signals", ["status"])
    op.create_index("ix_signals_created_at", "signals", ["created_at"])
    op.create_index("ix_signals_market_id", "signals", ["market_id"])

    # -----------------------------------------------------------------------
    # 5. paper_orders
    # -----------------------------------------------------------------------
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False, comment="YES or NO."),
        sa.Column("order_type", sa.String(length=10), nullable=False,
                  server_default="limit"),
        sa.Column("target_price", sa.Float(), nullable=False),
        sa.Column("size_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open",
                  comment="open | filled | partially_filled | cancelled | expired."),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_size_usd", sa.Float(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_po_market_status", "paper_orders", ["market_id", "status"])
    op.create_index("ix_po_placed_at", "paper_orders", ["placed_at"])
    op.create_index("ix_paper_orders_signal_id", "paper_orders", ["signal_id"])
    op.create_index("ix_paper_orders_status", "paper_orders", ["status"])

    # -----------------------------------------------------------------------
    # 6. positions
    # -----------------------------------------------------------------------
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False),
        sa.Column("side", sa.String(length=5), nullable=False, comment="YES or NO."),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("size_usd", sa.Float(), nullable=False,
                  comment="Original capital deployed in USD."),
        sa.Column("shares", sa.Float(), nullable=False,
                  comment="Number of outcome shares purchased (size_usd / entry_price)."),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="open",
                  comment="open | closed."),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(length=50), nullable=True,
                  comment="resolution | target_hit | stop_loss | manual | expired."),
        sa.Column("gross_pnl", sa.Float(), nullable=True,
                  comment="(exit_price - entry_price) * shares."),
        sa.Column("fees", sa.Float(), nullable=True,
                  comment="Estimated fees (~2% Polymarket taker fee)."),
        sa.Column("net_pnl", sa.Float(), nullable=True,
                  comment="gross_pnl - fees."),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_positions_market_strategy", "positions", ["market_id", "strategy"])
    op.create_index("ix_positions_status", "positions", ["status"])
    op.create_index("ix_positions_opened_at", "positions", ["opened_at"])
    op.create_index("ix_positions_market_id", "positions", ["market_id"])

    # -----------------------------------------------------------------------
    # 7. price_snapshots
    # -----------------------------------------------------------------------
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("crypto", sa.String(length=10), nullable=False,
                  comment="BTC, ETH, or SOL."),
        sa.Column("price_usd", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False,
                  comment="coingecko | binance."),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ps_crypto_ts", "price_snapshots", ["crypto", "timestamp"])
    op.create_index("ix_ps_timestamp", "price_snapshots", ["timestamp"])
    op.create_index("ix_price_snapshots_crypto", "price_snapshots", ["crypto"])

    # -----------------------------------------------------------------------
    # 8. system_state
    # -----------------------------------------------------------------------
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(length=100), nullable=False,
                  comment="Unique state key, e.g. 'last_scan_at', 'daily_pnl'."),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}",
                  comment="Arbitrary JSON payload."),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("key"),
    )

    # -----------------------------------------------------------------------
    # 9. strategy_configs
    # -----------------------------------------------------------------------
    op.create_table(
        "strategy_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("strategy", sa.String(length=50), nullable=False,
                  comment="Strategy name: volatility_spread | stink_bid | liquidity_sniper."),
        sa.Column("market_id", sa.Integer(), nullable=True,
                  comment="NULL = applies to all markets (default)."),
        sa.Column("crypto", sa.String(length=10), nullable=True,
                  comment="NULL = applies to all cryptos."),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true",
                  comment="Whether this strategy is active for the given scope."),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default="{}",
                  comment="Strategy-specific parameter overrides."),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sc_strategy_market", "strategy_configs", ["strategy", "market_id"])
    op.create_index("ix_strategy_configs_strategy", "strategy_configs", ["strategy"])
    op.create_index("ix_strategy_configs_market_id", "strategy_configs", ["market_id"])


# ---------------------------------------------------------------------------
# Downgrade — drop all tables in reverse dependency order
# ---------------------------------------------------------------------------


def downgrade() -> None:
    op.drop_table("strategy_configs")
    op.drop_table("system_state")
    op.drop_table("price_snapshots")
    op.drop_table("positions")
    op.drop_table("paper_orders")
    op.drop_table("signals")
    op.drop_table("observed_trades")
    op.drop_table("orderbook_snapshots")
    op.drop_table("markets")
