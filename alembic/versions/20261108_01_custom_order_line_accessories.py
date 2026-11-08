"""Attach custom-order stock usage to a product line when applicable.

Revision ID: 20261108_01
Revises: 20261107_01
Create Date: 2026-11-08
"""
from alembic import op
import sqlalchemy as sa


revision = "20261108_01"
down_revision = "20261107_01"
branch_labels = None
depends_on = None


FK_NAME = "fk_order_accessories_order_item_id_order_items"
INDEX_NAME = "ix_order_accessories_order_item_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "order_accessories" not in inspector.get_table_names() or "order_items" not in inspector.get_table_names():
        raise RuntimeError("Required order_accessories or order_items table is missing")

    columns = {column["name"] for column in inspector.get_columns("order_accessories")}
    foreign_keys = inspector.get_foreign_keys("order_accessories")
    indexes = {index["name"] for index in inspector.get_indexes("order_accessories")}

    needs_column = "order_item_id" not in columns
    has_order_item_fk = any(
        fk.get("constrained_columns") == ["order_item_id"]
        and fk.get("referred_table") == "order_items"
        and fk.get("referred_columns") == ["id"]
        for fk in foreign_keys
    )
    needs_fk = not has_order_item_fk
    needs_index = INDEX_NAME not in indexes
    if not (needs_column or needs_fk or needs_index):
        return

    with op.batch_alter_table("order_accessories") as batch_op:
        if needs_column:
            batch_op.add_column(sa.Column("order_item_id", sa.Integer(), nullable=True))
        if needs_fk:
            batch_op.create_foreign_key(
                FK_NAME,
                "order_items",
                ["order_item_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if needs_index:
            batch_op.create_index(INDEX_NAME, ["order_item_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "order_accessories" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("order_accessories")}
    if "order_item_id" not in columns:
        return

    foreign_keys = [
        fk for fk in inspector.get_foreign_keys("order_accessories")
        if fk.get("constrained_columns") == ["order_item_id"]
        and fk.get("referred_table") == "order_items"
    ]
    indexes = {index["name"] for index in inspector.get_indexes("order_accessories")}
    with op.batch_alter_table("order_accessories") as batch_op:
        if INDEX_NAME in indexes:
            batch_op.drop_index(INDEX_NAME)
        for foreign_key in foreign_keys:
            if foreign_key.get("name"):
                batch_op.drop_constraint(foreign_key["name"], type_="foreignkey")
        batch_op.drop_column("order_item_id")
