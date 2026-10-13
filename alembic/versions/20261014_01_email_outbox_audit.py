"""Add audit trail for controlled email outbox retries.

Revision ID: 20261014_01
Revises: 20261013_01
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "20261014_01"
down_revision = "20261013_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_outbox_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("email_outbox.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_outbox_audit_event_id", "email_outbox_audit", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_audit_event_id", table_name="email_outbox_audit")
    op.drop_table("email_outbox_audit")
