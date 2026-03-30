"""add volume_usd to markets table

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column(
            "volume_usd",
            sa.Float(),
            nullable=False,
            server_default="0.0",
            comment="Total traded volume in USD from Gamma API.",
        ),
    )


def downgrade() -> None:
    op.drop_column("markets", "volume_usd")
