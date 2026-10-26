"""Add owner-managed email sender identity settings.

Revision ID: 20261026_02
Revises: 20261026_01
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


revision = "20261026_02"
down_revision = "20261026_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_sender_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_email", sa.String(length=255), nullable=False),
        sa.Column("from_name", sa.String(length=100), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_email_sender_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("email_sender_settings")
