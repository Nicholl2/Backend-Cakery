"""Add transactional email outbox.

Revision ID: 20261013_01
Revises: 20261008_01
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20261013_01"
down_revision = "20261008_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_key", name="uq_email_outbox_event_key"),
    )
    op.create_index("ix_email_outbox_pending", "email_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_pending", table_name="email_outbox")
    op.drop_table("email_outbox")
