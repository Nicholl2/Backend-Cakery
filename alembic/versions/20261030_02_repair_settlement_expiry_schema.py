"""Repair settlement expiry columns and order status enum drift.

Revision ID: 20261030_02
Revises: 20261030_01
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = "20261030_02"
down_revision = "20261030_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Settlement expiry schema repair requires PostgreSQL")

    enum_exists = bind.execute(sa.text(
        "SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE n.nspname = current_schema() AND t.typname = 'orderstatusenum'"
    )).scalar_one_or_none()
    if not enum_exists:
        raise RuntimeError("Required PostgreSQL enum orderstatusenum is missing")

    # A database may have been stamped past 20261001_01 while only some of its
    # DDL actually ran. PostgreSQL enum changes need an autocommit block on
    # older supported server versions; the operation is idempotent for retries.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'cancelled_settlement_expired'")
    op.execute(sa.text(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_date TIMESTAMPTZ"
    ))
    op.execute(sa.text(
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS settlement_due_date TIMESTAMPTZ"
    ))


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum label. Keep the two nullable
    # columns as well, matching the repository's forward-repair policy.
    pass
