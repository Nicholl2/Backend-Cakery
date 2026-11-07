"""Link legacy product category labels to CRUD-managed categories.

Revision ID: 20261107_01
Revises: 20261106_02
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa


revision = "20261107_01"
down_revision = "20261106_02"
branch_labels = None
depends_on = None


_LEGACY_CATEGORY_ALIASES = {
    "cup cake": "cupcake",
}


def _category_key(value: str) -> str:
    normalized = " ".join(value.split()).casefold()
    return _LEGACY_CATEGORY_ALIASES.get(normalized, normalized)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"categories", "products"}.issubset(tables):
        raise RuntimeError("Required categories or products table is missing")

    product_columns = {column["name"] for column in inspector.get_columns("products")}
    if not {"category_id", "kategori"}.issubset(product_columns):
        raise RuntimeError("Products table must contain category_id and kategori columns")

    # CRUD-managed categories are the source of truth. Do not create new master
    # categories from arbitrary imported product text. Match case/spacing
    # variants and known aliases, and stop if the master list itself is
    # ambiguous after normalization rather than choosing an arbitrary ID.
    category_rows = bind.execute(sa.text(
        "SELECT id, name FROM categories ORDER BY id"
    )).all()
    category_ids: dict[str, int] = {}
    for row in category_rows:
        key = _category_key(row.name)
        if not key:
            continue
        if key in category_ids and category_ids[key] != row.id:
            raise RuntimeError(
                "Duplicate CRUD categories normalize to the same label; "
                "reconcile them before backfilling product categories"
            )
        category_ids[key] = row.id

    legacy_products = bind.execute(sa.text("""
        SELECT id, kategori
        FROM products
        WHERE category_id IS NULL
          AND kategori IS NOT NULL
          AND TRIM(kategori) <> ''
        ORDER BY id
    """)).all()
    for product in legacy_products:
        category_key = _category_key(product.kategori)
        category_id = category_ids.get(category_key)
        if category_id is not None:
            canonical_name = next(
                row.name for row in category_rows if row.id == category_id
            )
            bind.execute(
                sa.text(
                    "UPDATE products SET category_id = :category_id, "
                    "kategori = :category_name "
                    "WHERE id = :product_id AND category_id IS NULL"
                ),
                {
                    "category_id": category_id,
                    "category_name": canonical_name,
                    "product_id": product.id,
                },
            )


def downgrade() -> None:
    # Keep the product links; a downgrade cannot know if they were edited after
    # deployment, and the legacy labels remain available in products.kategori.
    pass
