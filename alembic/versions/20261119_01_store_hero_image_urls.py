"""Store an ordered list of homepage banner images.

Revision ID: 20261119_01
Revises: 20261108_01
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa


revision = "20261119_01"
down_revision = "20261108_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "store_customization" not in inspector.get_table_names():
        raise RuntimeError("Required store_customization table is missing")

    columns = {column["name"] for column in inspector.get_columns("store_customization")}
    if "hero_image_urls" not in columns:
        with op.batch_alter_table("store_customization") as batch_op:
            batch_op.add_column(sa.Column("hero_image_urls", sa.JSON(), nullable=True))

    # Backfill with a typed statement instead of a dialect-specific JSON
    # function, so the migration also runs on the SQLite test database.
    table = sa.table(
        "store_customization",
        sa.column("id", sa.Integer()),
        sa.column("hero_image_url", sa.Text()),
        sa.column("hero_image_urls", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(table.c.id, table.c.hero_image_url).where(
            table.c.hero_image_url.is_not(None),
            table.c.hero_image_urls.is_(None),
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            table.update()
            .where(table.c.id == row.id)
            .values(hero_image_urls=[row.hero_image_url])
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "store_customization" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("store_customization")}
    if "hero_image_urls" not in columns:
        return
    with op.batch_alter_table("store_customization") as batch_op:
        batch_op.drop_column("hero_image_urls")
