"""Add unpaid checkout expiry and fulfillment delay notification markers.

Revision ID: 20261026_01
Revises: 20261019_01
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


revision = "20261026_01"
down_revision = "20261019_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("payment_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("fulfillment_overdue_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_orders_payment_expires_at", "orders", ["payment_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_orders_payment_expires_at", table_name="orders")
    op.drop_column("orders", "fulfillment_overdue_notified_at")
    op.drop_column("orders", "payment_expires_at")
