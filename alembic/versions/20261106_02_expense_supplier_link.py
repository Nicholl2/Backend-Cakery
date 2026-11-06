"""Link operational expenses to suppliers.

Revision ID: 20261106_02
Revises: 20261106_01
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa


revision = "20261106_02"
down_revision = "20261106_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"expenses", "suppliers"}.issubset(tables):
        raise RuntimeError("Required expenses or suppliers table is missing")

    columns = {column["name"] for column in inspector.get_columns("expenses")}
    if "supplier_id" not in columns:
        op.add_column(
            "expenses",
            sa.Column("supplier_id", sa.Integer(), nullable=True),
        )

    orphan_count = bind.execute(sa.text(
        "SELECT count(*) FROM expenses e "
        "LEFT JOIN suppliers s ON s.id = e.supplier_id "
        "WHERE e.supplier_id IS NOT NULL AND s.id IS NULL"
    )).scalar_one()
    if orphan_count:
        raise RuntimeError(
            f"Cannot link expenses to suppliers: {orphan_count} orphan supplier_id value(s) found"
        )

    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys("expenses")
    has_supplier_fk = any(
        fk.get("constrained_columns") == ["supplier_id"]
        and fk.get("referred_table") == "suppliers"
        and fk.get("referred_columns") == ["id"]
        for fk in foreign_keys
    )
    if not has_supplier_fk:
        op.create_foreign_key(
            "fk_expenses_supplier_id_suppliers",
            "expenses",
            "suppliers",
            ["supplier_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("expenses")}
    if "ix_expenses_supplier_id" not in indexes:
        op.create_index("ix_expenses_supplier_id", "expenses", ["supplier_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "expenses" not in inspector.get_table_names():
        return

    indexes = {index["name"] for index in inspector.get_indexes("expenses")}
    if "ix_expenses_supplier_id" in indexes:
        op.drop_index("ix_expenses_supplier_id", table_name="expenses")

    foreign_keys = sa.inspect(bind).get_foreign_keys("expenses")
    if any(fk.get("name") == "fk_expenses_supplier_id_suppliers" for fk in foreign_keys):
        op.drop_constraint("fk_expenses_supplier_id_suppliers", "expenses", type_="foreignkey")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("expenses")}
    if "supplier_id" in columns:
        op.drop_column("expenses", "supplier_id")
