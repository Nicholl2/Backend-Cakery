"""Backfill sold counts and count completed product orders going forward.

Revision ID: 20261017_02
Revises: 20261017_01
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = "20261017_02"
down_revision = "20261017_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Product.sold_count was exposed by the API but never maintained. Rebuild it
    # from completed orders so existing catalog products reflect historical sales.
    op.execute(sa.text("""
        UPDATE products AS p
        SET sold_count = COALESCE((
            SELECT SUM(oi.jumlah)::INTEGER
            FROM order_items AS oi
            JOIN orders AS o ON o.id = oi.order_id
            WHERE oi.product_id = p.id
              AND o.status IN ('delivered', 'picked_up', 'completed')
        ), 0)
    """))


def downgrade() -> None:
    # Keep the corrected aggregate values; the downgrade cannot restore the
    # arbitrary values that may have been stored before this backfill.
    pass
