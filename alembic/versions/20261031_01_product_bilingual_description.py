"""Add English product description while retaining legacy Indonesian text.

Revision ID: 20261031_01
Revises: 20261030_02
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa


revision = "20261031_01"
down_revision = "20261030_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "deskripsi_en" not in {column["name"] for column in inspector.get_columns("products")}:
        op.add_column("products", sa.Column("deskripsi_en", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "deskripsi_en" in {column["name"] for column in inspector.get_columns("products")}:
        op.drop_column("products", "deskripsi_en")
