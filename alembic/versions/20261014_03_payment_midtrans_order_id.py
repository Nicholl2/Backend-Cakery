"""Persist the Midtrans order reference for each payment.

Revision ID: 20261014_03
Revises: 20261014_02
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_03"
down_revision = "20261014_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("pg_order_id", sa.String(100), nullable=True))
    op.create_index("uq_payments_pg_order_id", "payments", ["pg_order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_payments_pg_order_id", table_name="payments")
    op.drop_column("payments", "pg_order_id")
