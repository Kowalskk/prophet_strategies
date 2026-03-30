"""add bid_at_signal and ask_at_signal to signals table

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002_add_market_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column(
            "bid_at_signal",
            sa.Float(),
            nullable=True,
            comment="Best bid at the moment the signal was generated.",
        ),
    )
    op.add_column(
        "signals",
        sa.Column(
            "ask_at_signal",
            sa.Float(),
            nullable=True,
            comment="Best ask at the moment the signal was generated.",
        ),
    )


def downgrade() -> None:
    op.drop_column("signals", "ask_at_signal")
    op.drop_column("signals", "bid_at_signal")
