"""Add expense metadata, audit trail, and soft voiding.

Revision ID: 20261019_01
Revises: 20261017_03
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa


revision = "20261019_01"
down_revision = "20261017_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expenses", sa.Column("deskripsi", sa.String(length=500), nullable=True))
    op.add_column("expenses", sa.Column("penerima", sa.String(length=120), nullable=True))
    op.add_column("expenses", sa.Column("metode_pembayaran", sa.String(length=20), nullable=True))
    op.add_column("expenses", sa.Column("bukti_url", sa.String(length=2048), nullable=True))
    op.add_column(
        "expenses",
        sa.Column("is_voided", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("expenses", sa.Column("void_reason", sa.String(length=500), nullable=True))
    op.add_column(
        "expenses",
        sa.Column("voided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("expenses", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "expenses",
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_expenses_is_voided_tanggal", "expenses", ["is_voided", "tanggal"])
    op.create_check_constraint(
        "ck_expenses_payment_method",
        "expenses",
        "metode_pembayaran IS NULL OR metode_pembayaran IN ('cash', 'bank_transfer', 'e_wallet', 'card', 'other')",
    )
    op.create_check_constraint(
        "ck_expenses_void_metadata",
        "expenses",
        "NOT is_voided OR (void_reason IS NOT NULL AND voided_at IS NOT NULL)",
    )

    op.create_table(
        "expense_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "expense_id",
            sa.Integer(),
            sa.ForeignKey("expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('created', 'updated', 'voided')", name="ck_expense_audits_action"),
    )
    op.create_index("ix_expense_audits_expense_id", "expense_audits", ["expense_id"])


def downgrade() -> None:
    op.drop_index("ix_expense_audits_expense_id", table_name="expense_audits")
    op.drop_table("expense_audits")
    op.drop_constraint("ck_expenses_void_metadata", "expenses", type_="check")
    op.drop_constraint("ck_expenses_payment_method", "expenses", type_="check")
    op.drop_index("ix_expenses_is_voided_tanggal", table_name="expenses")
    op.drop_column("expenses", "updated_by")
    op.drop_column("expenses", "voided_at")
    op.drop_column("expenses", "voided_by")
    op.drop_column("expenses", "void_reason")
    op.drop_column("expenses", "is_voided")
    op.drop_column("expenses", "bukti_url")
    op.drop_column("expenses", "metode_pembayaran")
    op.drop_column("expenses", "penerima")
    op.drop_column("expenses", "deskripsi")
