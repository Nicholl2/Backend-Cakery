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
    with op.batch_alter_table("customers") as batch_op:
        batch_op.add_column(sa.Column("buyer_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_customers_buyer_id_buyers",
            "buyers",
            ["buyer_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_customers_buyer_id", "customers", ["buyer_id"], unique=True)
    # Preserve existing buyer/customer identity where current phone numbers
    # still match. Historical phone changes cannot be inferred safely here.
    op.execute(sa.text(
        "UPDATE customers AS c SET buyer_id = b.id "
        "FROM buyers AS b WHERE c.nomor_wa = b.phone"
    ))


def downgrade() -> None:
    op.drop_index("ix_customers_buyer_id", table_name="customers")
    op.drop_constraint("fk_customers_buyer_id_buyers", "customers", type_="foreignkey")
    op.drop_column("customers", "buyer_id")
