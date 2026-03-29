"""Add category column to markets table and make crypto nullable.

Revision ID: 0002_add_market_category
Revises: 0001_initial
Create Date: 2026-03-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_market_category"
down_revision: str = "0001_initial"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column("category", sa.String(50), nullable=True, index=True,
                  comment="Market category: crypto, sports, politics, entertainment, etc."),
    )
    op.alter_column("markets", "crypto", existing_type=sa.String(10), nullable=True)
    # Backfill existing markets as crypto
    op.execute("UPDATE markets SET category = 'crypto' WHERE category IS NULL")


def downgrade() -> None:
    op.drop_column("markets", "category")
    op.alter_column("markets", "crypto", existing_type=sa.String(10), nullable=False)
