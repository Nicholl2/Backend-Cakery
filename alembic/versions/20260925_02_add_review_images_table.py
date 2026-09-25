"""Add review_images table for multi-image product reviews

Revision ID: 20260925_02
Revises: 20260925_01
Create Date: 2026-09-25 20:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260925_02'
down_revision: Union[str, None] = '20260925_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create review_images table ───────────────────────────────────────────
    op.create_table(
        'review_images',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('review_id', sa.Integer(), sa.ForeignKey('reviews.id', ondelete='CASCADE'), nullable=False),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index('ix_review_images_id', 'review_images', ['id'], unique=False)
    op.create_index('ix_review_images_review_id', 'review_images', ['review_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_review_images_review_id', table_name='review_images')
    op.drop_index('ix_review_images_id', table_name='review_images')
    op.drop_table('review_images')
