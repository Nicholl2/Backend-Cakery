"""Add English question and answer to FAQ items.

Revision ID: 20261106_01
Revises: 20261031_01
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa


revision = "20261106_01"
down_revision = "20261031_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("faq_items")}
    if "pertanyaan_en" not in existing_columns:
        op.add_column("faq_items", sa.Column("pertanyaan_en", sa.Text(), nullable=True))
    if "jawaban_en" not in existing_columns:
        op.add_column("faq_items", sa.Column("jawaban_en", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("faq_items")}
    if "jawaban_en" in existing_columns:
        op.drop_column("faq_items", "jawaban_en")
    if "pertanyaan_en" in existing_columns:
        op.drop_column("faq_items", "pertanyaan_en")
