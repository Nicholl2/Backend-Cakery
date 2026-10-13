"""Add lease ownership fields to email outbox events.

Revision ID: 20261014_02
Revises: 20261014_01
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_02"
down_revision = "20261014_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_outbox", sa.Column("lease_token", sa.String(64), nullable=True))
    op.add_column("email_outbox", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_email_outbox_lease_expiry", "email_outbox", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_lease_expiry", table_name="email_outbox")
    op.drop_column("email_outbox", "lease_expires_at")
    op.drop_column("email_outbox", "lease_token")
