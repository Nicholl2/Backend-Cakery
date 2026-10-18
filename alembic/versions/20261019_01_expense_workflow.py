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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "expenses" not in tables:
        raise RuntimeError("Required expenses table is missing")

    # Some databases were bootstrapped from current SQLAlchemy metadata before
    # Alembic adopted them. Treat those columns as already migrated while still
    # adding missing fields to databases that genuinely predate this revision.
    existing_columns = {column["name"] for column in inspector.get_columns("expenses")}
    expense_columns = (
        ("deskripsi", sa.String(length=500), True, None),
        ("penerima", sa.String(length=120), True, None),
        ("metode_pembayaran", sa.String(length=20), True, None),
        ("bukti_url", sa.String(length=2048), True, None),
        ("is_voided", sa.Boolean(), False, sa.false()),
        ("void_reason", sa.String(length=500), True, None),
        ("voided_by", sa.Integer(), True, None),
        ("voided_at", sa.DateTime(timezone=True), True, None),
        ("updated_by", sa.Integer(), True, None),
    )
    for name, type_, nullable, server_default in expense_columns:
        if name in existing_columns:
            continue
        column = sa.Column(name, type_, nullable=nullable, server_default=server_default)
        if name in {"voided_by", "updated_by"}:
            column.append_foreign_key(sa.ForeignKey("users.id", ondelete="SET NULL"))
        op.add_column("expenses", column)

    inspector = sa.inspect(bind)
    expense_indexes = {index["name"] for index in inspector.get_indexes("expenses")}
    if "ix_expenses_is_voided_tanggal" not in expense_indexes:
        op.create_index("ix_expenses_is_voided_tanggal", "expenses", ["is_voided", "tanggal"])

    check_constraints = {
        constraint["name"] for constraint in sa.inspect(bind).get_check_constraints("expenses")
    }
    if "ck_expenses_payment_method" not in check_constraints:
        op.create_check_constraint(
            "ck_expenses_payment_method",
            "expenses",
            "metode_pembayaran IS NULL OR metode_pembayaran IN ('cash', 'bank_transfer', 'e_wallet', 'card', 'other')",
        )
    if "ck_expenses_void_metadata" not in check_constraints:
        op.create_check_constraint(
            "ck_expenses_void_metadata",
            "expenses",
            "NOT is_voided OR (void_reason IS NOT NULL AND voided_at IS NOT NULL)",
        )

    if "expense_audits" not in tables:
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
        )

    audit_checks = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("expense_audits")
    }
    if "ck_expense_audits_action" not in audit_checks:
        op.create_check_constraint(
            "ck_expense_audits_action",
            "expense_audits",
            "action IN ('created', 'updated', 'voided')",
        )
    audit_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("expense_audits")}
    if "ix_expense_audits_expense_id" not in audit_indexes:
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
