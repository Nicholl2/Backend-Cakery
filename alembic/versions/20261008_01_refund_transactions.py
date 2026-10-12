"""Add auditable refund transactions.

Revision ID: 20261008_01
Revises: 20261005_01
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_01"
down_revision = "20261005_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    refund_status = sa.Enum("pending", "completed", "failed", name="refundstatusenum")
    refund_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "refund_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("refund_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", refund_status, nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("gateway_ref", sa.String(length=100), nullable=True, unique=True),
        sa.Column("initiated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("initiated_source", sa.String(length=32), nullable=False, server_default="seller"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refund_transactions_id", "refund_transactions", ["id"])
    op.create_index("ix_refund_transactions_payment_id", "refund_transactions", ["payment_id"])
    op.execute(sa.text("""
        INSERT INTO refund_transactions
            (payment_id, refund_amount, status, reason, initiated_source, created_at, completed_at)
        SELECT id, jumlah_bayar, 'completed', 'Legacy full refund migrated from payment status',
               'legacy_migration', COALESCE(updated_at, created_at), COALESCE(updated_at, created_at)
        FROM payments
        WHERE lower(CAST(payment_status AS TEXT)) = 'refunded'
    """))


def downgrade() -> None:
    op.drop_index("ix_refund_transactions_payment_id", table_name="refund_transactions")
    op.drop_index("ix_refund_transactions_id", table_name="refund_transactions")
    op.drop_table("refund_transactions")
    sa.Enum(name="refundstatusenum").drop(op.get_bind(), checkfirst=True)
