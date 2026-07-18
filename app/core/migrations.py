from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_product_columns(conn: AsyncConnection):
    """
    Ensure new catalog columns are present in the 'products' table on PostgreSQL database.
    Updates existing null values to defaults and applies constraints/indices.
    """
    # Only run on PostgreSQL dialect
    if conn.dialect.name != "postgresql":
        return

    # 1. Add columns if they do not exist
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS slug VARCHAR(100);"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS rating DOUBLE PRECISION DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS review_count INTEGER DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS sold_count INTEGER DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS rasa VARCHAR(100);"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS ukuran_atau_isi VARCHAR(100);"))

    # 2. Update existing NULL records to their defaults before applying NOT NULL constraint
    await conn.execute(text("UPDATE products SET rating = COALESCE(rating, 0.0) WHERE rating IS NULL;"))
    await conn.execute(text("UPDATE products SET review_count = COALESCE(review_count, 0) WHERE review_count IS NULL;"))
    await conn.execute(text("UPDATE products SET sold_count = COALESCE(sold_count, 0) WHERE sold_count IS NULL;"))
    await conn.execute(text("UPDATE products SET is_featured = COALESCE(is_featured, FALSE) WHERE is_featured IS NULL;"))

    # 3. Apply NOT NULL constraints to the default-valued columns
    await conn.execute(text("ALTER TABLE products ALTER COLUMN rating SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN review_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN sold_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN is_featured SET NOT NULL;"))

    # 4. Create unique partial index on slug (to allow multiple NULLs but unique values when present)
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_products_slug ON products (slug) WHERE slug IS NOT NULL;"
    ))
