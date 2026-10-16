"""Link buyer accounts to stable customer records.

Revision ID: 20261016_02
Revises: 20261016_01
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa


revision = "20261016_02"
down_revision = "20261016_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CI/bootstrap can call Base.metadata.create_all() before Alembic. In that
    # case the current model has already created some or all of these objects.
    # Detect by structure (not only our constraint name) so the migration also
    # handles databases where the FK was generated with a dialect-specific name.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("customers")}
    foreign_keys = inspector.get_foreign_keys("customers")
    has_buyer_fk = any(
        fk.get("constrained_columns") == ["buyer_id"]
        and fk.get("referred_table") == "buyers"
        and fk.get("referred_columns") == ["id"]
        for fk in foreign_keys
    )

    if "buyer_id" not in columns or not has_buyer_fk:
        with op.batch_alter_table("customers") as batch_op:
            if "buyer_id" not in columns:
                batch_op.add_column(sa.Column("buyer_id", sa.Integer(), nullable=True))
            if not has_buyer_fk:
                batch_op.create_foreign_key(
                    "fk_customers_buyer_id_buyers",
                    "buyers",
                    ["buyer_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    # The model's unique/index=True declaration uses this same index name, but
    # inspect column coverage too in case create_all used an equivalent index.
    indexes = inspector.get_indexes("customers")
    unique_constraints = inspector.get_unique_constraints("customers")
    has_unique_buyer = any(
        index.get("column_names") == ["buyer_id"] and index.get("unique")
        for index in indexes
    ) or any(
        constraint.get("column_names") == ["buyer_id"]
        for constraint in unique_constraints
    )
    if not has_unique_buyer:
        op.create_index("ix_customers_buyer_id", "customers", ["buyer_id"], unique=True)
    # Preserve existing buyer/customer identity where current phone numbers
    # still match. Historical phone changes cannot be inferred safely here.
    op.execute(sa.text(
        "UPDATE customers AS c SET buyer_id = b.id "
        "FROM buyers AS b WHERE c.nomor_wa = b.phone"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("customers")}
    if "buyer_id" not in columns:
        return

    for index in inspector.get_indexes("customers"):
        if index.get("column_names") == ["buyer_id"]:
            op.drop_index(index["name"], table_name="customers")
    foreign_keys = inspector.get_foreign_keys("customers")
    fk = next((
        item for item in foreign_keys
        if item.get("constrained_columns") == ["buyer_id"]
        and item.get("referred_table") == "buyers"
    ), None)
    with op.batch_alter_table("customers") as batch_op:
        if fk and fk.get("name"):
            batch_op.drop_constraint(fk["name"], type_="foreignkey")
        batch_op.drop_column("buyer_id")
