"""Add inventory movement ledger.

Revision ID: 20261005_01
Revises: 20261001_01
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "20261005_01"
down_revision = "20261001_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text("""
        SELECT lower(trim(nama_item)) AS normalized_name, count(*) AS row_count
        FROM stock_items GROUP BY lower(trim(nama_item)) HAVING count(*) > 1
    """)).all()
    if duplicates:
        names = ", ".join(row.normalized_name for row in duplicates)
        raise RuntimeError(
            "Duplicate stock master names must be reconciled before inventory migration: " + names
        )

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_item_id", sa.Integer(), sa.ForeignKey("stock_items.id"), nullable=False),
        sa.Column("movement_type", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("reference_type", sa.String(length=32), nullable=True),
        sa.Column("reference_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_stock_movements_stock_item_id", "stock_movements", ["stock_item_id"])
    op.create_index("uq_stock_items_normalized_name", "stock_items", [sa.text("lower(trim(nama_item))")], unique=True)
    op.execute(sa.text("""
        INSERT INTO stock_movements
            (stock_item_id, movement_type, quantity, unit_cost, reference_type, reference_id, reason)
        SELECT id, 'OPENING_BALANCE', stok_tersedia, harga_per_satuan, 'migration', id,
               'Saldo awal saat ledger inventory diaktifkan'
        FROM stock_items
        WHERE COALESCE(stok_tersedia, 0) > 0
    """))
    # Preserve legacy supplier_id values in place: they cannot be safely
    # reconstructed as historical purchases because no quantity/price/date exists.


def downgrade() -> None:
    op.drop_index("uq_stock_items_normalized_name", table_name="stock_items")
    op.drop_index("ix_stock_movements_stock_item_id", table_name="stock_movements")
    op.drop_table("stock_movements")
