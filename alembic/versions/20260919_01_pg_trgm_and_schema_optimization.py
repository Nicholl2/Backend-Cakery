"""Enable pg_trgm extension, GIN Trigram indexes, and schema optimizations

Revision ID: 20260919_01
Revises: 
Create Date: 2026-09-19 20:28:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260919_01'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. PostgreSQL Extension: pg_trgm ───────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # ── 2. GIN Trigram Indexes for Substring & ILIKE Searches ──────────────────
    gin_indexes = [
        ("ix_products_nama_produk_trgm", "products", "nama_produk"),
        ("ix_products_deskripsi_trgm", "products", "deskripsi"),
        ("ix_categories_name_trgm", "categories", "name"),
        ("ix_users_username_trgm", "users", "username"),
        ("ix_users_email_trgm", "users", "email"),
        ("ix_buyers_name_trgm", "buyers", "name"),
        ("ix_buyers_email_trgm", "buyers", "email"),
        ("ix_buyers_phone_trgm", "buyers", "phone"),
        ("ix_customers_nama_trgm", "customers", "nama"),
        ("ix_customers_nomor_wa_trgm", "customers", "nomor_wa"),
        ("ix_stock_items_nama_item_trgm", "stock_items", "nama_item"),
        ("ix_suppliers_nama_supplier_trgm", "suppliers", "nama_supplier"),
    ]
    for idx_name, table_name, col_name in gin_indexes:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} USING gin ({col_name} gin_trgm_ops);"
        )

    # ── 3. Convert VARCHAR(500)/VARCHAR(1000) to TEXT ──────────────────────────
    text_columns = [
        ("categories", "description"),
        ("products", "deskripsi"),
        ("products", "image_url"),
        ("buyers", "avatar_url"),
        ("users", "avatar_url"),
        ("orders", "notes"),
    ]
    for table_name, col_name in text_columns:
        op.execute(f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE TEXT;")

    # ── 4. Standardize Rating to NUMERIC(3, 2) ─────────────────────────────────
    op.execute(
        "ALTER TABLE products ALTER COLUMN rating TYPE NUMERIC(3, 2) USING rating::NUMERIC(3, 2);"
    )

    # ── 5. Enforce NOT NULL and Defaults on Boolean Flags ─────────────────────
    boolean_cleanups = [
        ("products", "is_active", "TRUE"),
        ("users", "is_active", "TRUE"),
        ("customers", "is_verified", "FALSE"),
        ("suppliers", "is_active", "TRUE"),
        ("purchases", "is_received", "FALSE"),
        ("faq_items", "is_active", "TRUE"),
    ]
    for table_name, col_name, default_val in boolean_cleanups:
        op.execute(
            f"UPDATE {table_name} SET {col_name} = COALESCE({col_name}, {default_val}) WHERE {col_name} IS NULL;"
        )
        op.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET DEFAULT {default_val};"
        )
        op.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET NOT NULL;"
        )


def downgrade() -> None:
    # Drop GIN Trigram indexes
    gin_indexes = [
        ("ix_products_nama_produk_trgm", "products"),
        ("ix_products_deskripsi_trgm", "products"),
        ("ix_categories_name_trgm", "categories"),
        ("ix_users_username_trgm", "users"),
        ("ix_users_email_trgm", "users"),
        ("ix_buyers_name_trgm", "buyers"),
        ("ix_buyers_email_trgm", "buyers"),
        ("ix_buyers_phone_trgm", "buyers"),
        ("ix_customers_nama_trgm", "customers"),
        ("ix_customers_nomor_wa_trgm", "customers"),
        ("ix_stock_items_nama_item_trgm", "stock_items"),
        ("ix_suppliers_nama_supplier_trgm", "suppliers"),
    ]
    for idx_name, table_name in gin_indexes:
        op.execute(f"DROP INDEX IF EXISTS {idx_name};")

    # Revert TEXT columns
    op.execute("ALTER TABLE categories ALTER COLUMN description TYPE VARCHAR(500);")
    op.execute("ALTER TABLE products ALTER COLUMN deskripsi TYPE VARCHAR(500);")
    op.execute("ALTER TABLE products ALTER COLUMN image_url TYPE VARCHAR(500);")
    op.execute("ALTER TABLE buyers ALTER COLUMN avatar_url TYPE VARCHAR(500);")
    op.execute("ALTER TABLE users ALTER COLUMN avatar_url TYPE VARCHAR(500);")
    op.execute("ALTER TABLE orders ALTER COLUMN notes TYPE VARCHAR(1000);")

    # Revert rating
    op.execute("ALTER TABLE products ALTER COLUMN rating TYPE DOUBLE PRECISION USING rating::DOUBLE PRECISION;")
