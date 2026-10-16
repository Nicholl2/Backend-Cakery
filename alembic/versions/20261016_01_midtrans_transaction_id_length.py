"""Allow Midtrans transaction IDs up to 255 characters.

Revision ID: 20261016_01
Revises: 20261015_01
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = "20261016_01"
down_revision = "20261015_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "payments",
        "pg_transaction_id",
        existing_type=sa.String(length=100),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    long_ids = bind.execute(sa.text(
        "SELECT count(*) FROM payments WHERE length(pg_transaction_id) > 100"
    )).scalar_one()
    if long_ids:
        raise RuntimeError(
            "Cannot downgrade pg_transaction_id to VARCHAR(100): "
            f"{long_ids} transaction ID(s) exceed that length."
        )
    op.alter_column(
        "payments",
        "pg_transaction_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
