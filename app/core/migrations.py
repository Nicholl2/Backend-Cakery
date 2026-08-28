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
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS minimum_order INTEGER DEFAULT 1;"))

    # 2. Update existing NULL records to their defaults before applying NOT NULL constraint
    await conn.execute(text("UPDATE products SET rating = COALESCE(rating, 0.0) WHERE rating IS NULL;"))
    await conn.execute(text("UPDATE products SET review_count = COALESCE(review_count, 0) WHERE review_count IS NULL;"))
    await conn.execute(text("UPDATE products SET sold_count = COALESCE(sold_count, 0) WHERE sold_count IS NULL;"))
    await conn.execute(text("UPDATE products SET is_featured = COALESCE(is_featured, FALSE) WHERE is_featured IS NULL;"))
    await conn.execute(text("UPDATE products SET minimum_order = COALESCE(minimum_order, 1) WHERE minimum_order IS NULL;"))

    # 3. Apply NOT NULL constraints to the default-valued columns
    await conn.execute(text("ALTER TABLE products ALTER COLUMN rating SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN review_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN sold_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN is_featured SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN minimum_order SET NOT NULL;"))

    # 4. Create unique partial index on slug (to allow multiple NULLs but unique values when present)
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_products_slug ON products (slug) WHERE slug IS NOT NULL;"
    ))


async def ensure_buyer_columns(conn: AsyncConnection):
    """
    Ensure is_active column is present in the 'buyers' table on PostgreSQL database.
    """
    # Only run on PostgreSQL dialect
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE buyers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"))
    await conn.execute(text("UPDATE buyers SET is_active = COALESCE(is_active, TRUE) WHERE is_active IS NULL;"))
    await conn.execute(text("ALTER TABLE buyers ALTER COLUMN is_active SET NOT NULL;"))


async def ensure_stock_item_columns(conn: AsyncConnection):
    """
    Ensure alert_min_stok and supplier_id columns are present in the 'stock_items' table on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS alert_min_stok NUMERIC(10, 2) DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS supplier_id INTEGER REFERENCES suppliers(id);"))
    await conn.execute(text("UPDATE stock_items SET alert_min_stok = COALESCE(alert_min_stok, 0) WHERE alert_min_stok IS NULL;"))
    await conn.execute(text("ALTER TABLE stock_items ALTER COLUMN alert_min_stok SET NOT NULL;"))


async def ensure_recipe_columns(conn: AsyncConnection):
    """
    Ensure quantity_required and unit columns are present in the 'recipes' table on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS quantity_required NUMERIC(10, 4);"))
    await conn.execute(text("ALTER TABLE recipes ADD COLUMN IF NOT EXISTS unit VARCHAR(20);"))
    
    # Update quantity_required to jumlah_dibutuhkan if null
    await conn.execute(text("UPDATE recipes SET quantity_required = COALESCE(quantity_required, jumlah_dibutuhkan) WHERE quantity_required IS NULL;"))
    
    # Update unit to match the stock item's satuan if null
    await conn.execute(text(
        "UPDATE recipes SET unit = s.satuan FROM stock_items s WHERE recipes.stock_item_id = s.id AND recipes.unit IS NULL;"
    ))


async def ensure_otp_columns(conn: AsyncConnection):
    """
    Ensure new WA deep link OTP columns are present in the 'otp_codes' table on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE otp_codes ADD COLUMN IF NOT EXISTS nonce VARCHAR(50);"))
    await conn.execute(text("ALTER TABLE otp_codes ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20);"))
    await conn.execute(text("ALTER TABLE otp_codes ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;"))
    await conn.execute(text("ALTER TABLE otp_codes ADD COLUMN IF NOT EXISTS verify_token VARCHAR(36);"))
    
    # Update existing NULL records for is_verified to FALSE
    await conn.execute(text("UPDATE otp_codes SET is_verified = COALESCE(is_verified, FALSE) WHERE is_verified IS NULL;"))
    await conn.execute(text("ALTER TABLE otp_codes ALTER COLUMN is_verified SET NOT NULL;"))

    # Create indices
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_otp_codes_nonce ON otp_codes (nonce) WHERE nonce IS NOT NULL;"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_otp_codes_verify_token ON otp_codes (verify_token) WHERE verify_token IS NOT NULL;"))
