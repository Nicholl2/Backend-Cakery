"""Add product_images table and migrate existing image_url data

Revision ID: 20260925_01
Revises: 20260919_01
Create Date: 2026-09-25 16:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260925_01'
down_revision: Union[str, None] = '20260919_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create product_images table ──────────────────────────────────────────
    op.create_table(
        'product_images',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index('ix_product_images_id', 'product_images', ['id'], unique=False)
    op.create_index('ix_product_images_product_id', 'product_images', ['product_id'], unique=False)

    # ── 2. Data Migration: Backfill existing products.image_url ─────────────────
    op.execute(
        """
        INSERT INTO product_images (product_id, image_url, is_primary, created_at)
        SELECT id, image_url, TRUE, NOW()
        FROM products
        WHERE image_url IS NOT NULL AND TRIM(image_url) != '';
        """
    )


def downgrade() -> None:
    op.drop_index('ix_product_images_product_id', table_name='product_images')
    op.drop_index('ix_product_images_id', table_name='product_images')
    op.drop_table('product_images')
