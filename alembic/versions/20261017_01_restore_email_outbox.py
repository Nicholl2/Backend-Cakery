"""Restore the email outbox tables when a stamped database is missing them.

Revision ID: 20261017_01
Revises: 20261016_02
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa


revision = "20261017_01"
down_revision = "20261016_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "email_outbox" not in tables:
        op.create_table(
            "email_outbox",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_key", sa.String(200), nullable=False),
            sa.Column("recipient", sa.String(255), nullable=False),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("lease_token", sa.String(64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("event_key", name="uq_email_outbox_event_key"),
        )
        tables.add("email_outbox")
    else:
        columns = {column["name"] for column in inspector.get_columns("email_outbox")}
        with op.batch_alter_table("email_outbox") as batch_op:
            if "lease_token" not in columns:
                batch_op.add_column(sa.Column("lease_token", sa.String(64), nullable=True))
            if "lease_expires_at" not in columns:
                batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))

    if "email_outbox_audit" not in tables:
        op.create_table(
            "email_outbox_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "event_id", sa.Integer(),
                sa.ForeignKey("email_outbox.id"), nullable=False,
            ),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("actor", sa.String(100), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.func.now(),
            ),
        )

    inspector = sa.inspect(bind)
    outbox_indexes = {index["name"] for index in inspector.get_indexes("email_outbox")}
    if "ix_email_outbox_pending" not in outbox_indexes:
        op.create_index(
            "ix_email_outbox_pending", "email_outbox", ["status", "next_attempt_at"]
        )
    if "ix_email_outbox_lease_expiry" not in outbox_indexes:
        op.create_index(
            "ix_email_outbox_lease_expiry", "email_outbox", ["status", "lease_expires_at"]
        )

    audit_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("email_outbox_audit")
    }
    if "ix_email_outbox_audit_event_id" not in audit_indexes:
        op.create_index(
            "ix_email_outbox_audit_event_id", "email_outbox_audit", ["event_id"]
        )


def downgrade() -> None:
    # This migration may restore a live queue containing unsent email events.
    # Keep the recovered tables and their data if a downgrade is requested.
    pass
