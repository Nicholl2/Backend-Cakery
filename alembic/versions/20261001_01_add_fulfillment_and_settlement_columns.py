"""Add fulfillment_date and settlement_due_date columns to orders table

Revision ID: 20261001_01
Revises: 20260925_02_add_review_images_table
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20261001_01'
down_revision = '20260925_02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add fulfillment_date column
    op.add_column('orders', sa.Column('fulfillment_date', sa.DateTime(timezone=True), nullable=True))
    # Add settlement_due_date column
    op.add_column('orders', sa.Column('settlement_due_date', sa.DateTime(timezone=True), nullable=True))
    
    # Add cancelled_settlement_expired to orderstatusenum (PostgreSQL)
    op.execute("ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'cancelled_settlement_expired'")


def downgrade() -> None:
    op.drop_column('orders', 'settlement_due_date')
    op.drop_column('orders', 'fulfillment_date')
    # Note: PostgreSQL doesn't support removing enum values easily
