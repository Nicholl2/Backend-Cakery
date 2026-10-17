"""Add cancellation audit fields for purchase orders.

Revision ID: 20261017_03
Revises: 20261017_02
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = "20261017_03"
down_revision = "20261017_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchases",
        sa.Column("is_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("purchases", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("purchases", sa.Column("cancellation_reason", sa.Text(), nullable=True))
    op.add_column(
        "purchases",
        sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "purchase_items",
        sa.Column("cost_deviation_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_purchases_not_received_and_cancelled",
        "purchases",
        "NOT (is_received AND is_cancelled)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchases_not_received_and_cancelled", "purchases", type_="check")
    op.drop_column("purchase_items", "cost_deviation_confirmed")
    op.drop_column("purchases", "cancellation_reason")
    op.drop_column("purchases", "cancelled_by")
    op.drop_column("purchases", "cancelled_at")
    op.drop_column("purchases", "is_cancelled")
