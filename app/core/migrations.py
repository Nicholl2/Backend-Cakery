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
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS slug VARCHAR(100);"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS rating DOUBLE PRECISION DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS review_count INTEGER DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS sold_count INTEGER DEFAULT 0;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_featured BOOLEAN DEFAULT FALSE;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS minimum_order INTEGER DEFAULT 1;"))
    await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_available BOOLEAN DEFAULT TRUE;"))

    # 2. Update existing NULL records to their defaults before applying NOT NULL constraint
    await conn.execute(text("UPDATE products SET rating = COALESCE(rating, 0.0) WHERE rating IS NULL;"))
    await conn.execute(text("UPDATE products SET review_count = COALESCE(review_count, 0) WHERE review_count IS NULL;"))
    await conn.execute(text("UPDATE products SET sold_count = COALESCE(sold_count, 0) WHERE sold_count IS NULL;"))
    await conn.execute(text("UPDATE products SET is_featured = COALESCE(is_featured, FALSE) WHERE is_featured IS NULL;"))
    await conn.execute(text("UPDATE products SET minimum_order = COALESCE(minimum_order, 1) WHERE minimum_order IS NULL;"))
    await conn.execute(text("UPDATE products SET is_available = COALESCE(is_available, TRUE) WHERE is_available IS NULL;"))

    # 3. Apply NOT NULL constraints to the default-valued columns
    await conn.execute(text("ALTER TABLE products ALTER COLUMN rating SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN review_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN sold_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN is_featured SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN minimum_order SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE products ALTER COLUMN is_available SET NOT NULL;"))

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
    await conn.execute(text("ALTER TABLE buyers ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);"))
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
    await conn.execute(text("ALTER TABLE otp_codes ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0;"))
    
    # Update existing NULL records for is_verified to FALSE
    await conn.execute(text("UPDATE otp_codes SET is_verified = COALESCE(is_verified, FALSE) WHERE is_verified IS NULL;"))
    await conn.execute(text("ALTER TABLE otp_codes ALTER COLUMN is_verified SET NOT NULL;"))

    # Ensure attempt_count has NOT NULL constraint with default 0
    await conn.execute(text("UPDATE otp_codes SET attempt_count = 0 WHERE attempt_count IS NULL;"))
    await conn.execute(text("ALTER TABLE otp_codes ALTER COLUMN attempt_count SET NOT NULL;"))
    await conn.execute(text("ALTER TABLE otp_codes ALTER COLUMN attempt_count SET DEFAULT 0;"))

    # Create indices
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_otp_codes_nonce ON otp_codes (nonce) WHERE nonce IS NOT NULL;"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_otp_codes_verify_token ON otp_codes (verify_token) WHERE verify_token IS NOT NULL;"))


async def ensure_user_columns(conn: AsyncConnection):
    """
    Ensure email and phone_number columns exist in the 'users' table on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(100);"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20);"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email) WHERE email IS NOT NULL;"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number) WHERE phone_number IS NOT NULL;"))


async def ensure_order_columns(conn: AsyncConnection):
    """
    Ensure notes, due_date, payment_method_preference exist in 'orders' table,
    and custom_product_name exists with nullable product_id in 'order_items' table.
    """
    if conn.dialect.name != "postgresql":
        return

    # Order columns
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS notes VARCHAR(1000);"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS due_date TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_method_preference VARCHAR(50);"))

    # OrderItem columns & nullable product_id
    await conn.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS custom_product_name VARCHAR(255);"))
    await conn.execute(text("ALTER TABLE order_items ALTER COLUMN product_id DROP NOT NULL;"))

    # Invoice column length
    await conn.execute(text("ALTER TABLE invoices ALTER COLUMN nomor_invoice TYPE VARCHAR(50);"))



async def ensure_order_status_enum(conn: AsyncConnection):
    """
    Ensure 'refunded' and 'completed' values exist in PostgreSQL enum 'orderstatusenum'.
    PostgreSQL requires ALTER TYPE ADD VALUE to run outside transaction blocks (autocommit mode).
    """
    if conn.dialect.name != "postgresql":
        return
    try:
        autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for val in ["completed", "refunded"]:
            await autocommit_conn.execute(text(
                f"ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS '{val}';"
            ))
    except Exception:
        # Fallback to independent connection if the passed connection was inside an existing transaction
        try:
            async with conn.engine.connect() as auto_conn:
                autocommit_conn = await auto_conn.execution_options(isolation_level="AUTOCOMMIT")
                for val in ["completed", "refunded"]:
                    await autocommit_conn.execute(text(
                        f"ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS '{val}';"
                    ))
        except Exception:
            pass


async def ensure_payment_columns(conn: AsyncConnection):
    """
    Ensure settled_at, updated_at, and notes columns are present in the 'payments' table on PostgreSQL database.
    Backfills existing payments with created_at if settled_at is NULL.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS notes TEXT;"))
    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;"))
    try:
        await conn.execute(text("UPDATE payments SET settled_at = created_at WHERE settled_at IS NULL;"))
    except Exception:
        pass


async def ensure_review_columns(conn: AsyncConnection):
    """
    Ensure order_id column and unique constraint exist on 'reviews' table on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE reviews ADD COLUMN IF NOT EXISTS order_id INTEGER REFERENCES orders(id);"))
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_order_product_customer ON reviews (order_id, product_id, customer_id) WHERE order_id IS NOT NULL;"
    ))


async def ensure_trigram_and_schema_optimizations(conn: AsyncConnection):
    """
    Ensure PostgreSQL pg_trgm extension, GIN Trigram indexes, TEXT column types,
    Numeric rating precision, and NOT NULL constraints on status flags.
    """
    if conn.dialect.name != "postgresql":
        return

    # 1. Enable pg_trgm extension
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))

    # 2. GIN Trigram Indexes for accelerated ILIKE '%query%' substring searches
    indexes = [
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
    for idx_name, table_name, col_name in indexes:
        try:
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} USING gin ({col_name} gin_trgm_ops);"
            ))
        except Exception:
            pass

    # 3. Optimize column types to TEXT
    type_conversions = [
        ("categories", "description"),
        ("products", "deskripsi"),
        ("products", "image_url"),
        ("buyers", "avatar_url"),
        ("users", "avatar_url"),
        ("orders", "notes"),
    ]
    for table_name, col_name in type_conversions:
        try:
            await conn.execute(text(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE TEXT;"
            ))
        except Exception:
            pass

    # 4. Rating precision conversion (DOUBLE PRECISION/FLOAT -> NUMERIC(3, 2))
    try:
        await conn.execute(text(
            "ALTER TABLE products ALTER COLUMN rating TYPE NUMERIC(3, 2) USING rating::NUMERIC(3, 2);"
        ))
    except Exception:
        pass

    # 5. Enforce NOT NULL constraints and defaults on status/boolean flags
    boolean_cleanups = [
        ("products", "is_active", "TRUE"),
        ("users", "is_active", "TRUE"),
        ("customers", "is_verified", "FALSE"),
        ("suppliers", "is_active", "TRUE"),
        ("purchases", "is_received", "FALSE"),
        ("faq_items", "is_active", "TRUE"),
    ]
    for table_name, col_name, default_val in boolean_cleanups:
        try:
            await conn.execute(text(
                f"UPDATE {table_name} SET {col_name} = COALESCE({col_name}, {default_val}) WHERE {col_name} IS NULL;"
            ))
            await conn.execute(text(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET DEFAULT {default_val};"
            ))
            await conn.execute(text(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET NOT NULL;"
            ))
        except Exception:
            pass


async def ensure_product_images_table(conn: AsyncConnection):
    """
    Ensure 'product_images' table exists and migrate existing image_url data from 'products' table.
    """
    if conn.dialect.name != "postgresql":
        return

    # 1. Create table if not exists
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS product_images (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            image_url TEXT NOT NULL,
            is_primary BOOLEAN DEFAULT FALSE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))

    # 2. Indices
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_images_id ON product_images (id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_product_images_product_id ON product_images (product_id);"))

    # 3. Data migration: Migrate existing products.image_url if not already in product_images
    try:
        await conn.execute(text("""
            INSERT INTO product_images (product_id, image_url, is_primary, created_at)
            SELECT p.id, p.image_url, TRUE, NOW()
            FROM products p
            WHERE p.image_url IS NOT NULL 
              AND TRIM(p.image_url) != ''
              AND NOT EXISTS (
                  SELECT 1 FROM product_images pi WHERE pi.product_id = p.id
              );
        """))
    except Exception:
        pass


async def run_auto_migrations(conn: AsyncConnection):
    """
    Wrapper to run all auto-migrations sequentially on a given connection.
    """
    await ensure_product_columns(conn)
    await ensure_buyer_columns(conn)
    await ensure_stock_item_columns(conn)
    await ensure_recipe_columns(conn)
    await ensure_otp_columns(conn)
    await ensure_user_columns(conn)
    await ensure_order_columns(conn)
    await ensure_payment_columns(conn)
    await ensure_review_columns(conn)
    await ensure_trigram_and_schema_optimizations(conn)
    await ensure_product_images_table(conn)







