"""Add stock and price snapshots for seller order accessories.

Revision ID: 20261015_01
Revises: 20261014_03
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "20261015_01"
down_revision = "20261014_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_items", sa.Column("product_name_snapshot", sa.String(length=255), nullable=True))
    op.add_column("order_items", sa.Column("unit_price_snapshot", sa.Numeric(10, 2), nullable=True))
    op.create_table(
        "order_accessories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=False),
        sa.Column("stock_item_name", sa.String(length=100), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 4), nullable=False),
        sa.Column("unit_cost_snapshot", sa.Numeric(10, 4), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index("ix_order_accessories_order_id", "order_accessories", ["order_id"])
    op.create_index("ix_order_accessories_stock_item_id", "order_accessories", ["stock_item_id"])


def downgrade() -> None:
    op.drop_index("ix_order_accessories_stock_item_id", table_name="order_accessories")
    op.drop_index("ix_order_accessories_order_id", table_name="order_accessories")
    op.drop_table("order_accessories")
    op.drop_column("order_items", "unit_price_snapshot")
    op.drop_column("order_items", "product_name_snapshot")
