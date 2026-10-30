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
    Ensure alert_min_stok is present on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE stock_items ADD COLUMN IF NOT EXISTS alert_min_stok NUMERIC(10, 2) DEFAULT 0;"))
    await conn.execute(text("UPDATE stock_items SET alert_min_stok = COALESCE(alert_min_stok, 0) WHERE alert_min_stok IS NULL;"))
    await conn.execute(text("ALTER TABLE stock_items ALTER COLUMN alert_min_stok SET NOT NULL;"))


async def ensure_purchase_cancellation_columns(conn: AsyncConnection):
    """Ensure pending purchase cancellations remain visible for audit."""
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text(
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN DEFAULT FALSE;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS cancellation_reason TEXT;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS cancelled_by INTEGER REFERENCES users(id) ON DELETE SET NULL;"
    ))
    await conn.execute(text(
        "UPDATE purchases SET is_cancelled = FALSE WHERE is_cancelled IS NULL;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchases ALTER COLUMN is_cancelled SET DEFAULT FALSE;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchases ALTER COLUMN is_cancelled SET NOT NULL;"
    ))
    await conn.execute(text("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_purchases_not_received_and_cancelled'
                  AND conrelid = 'purchases'::regclass
            ) THEN
                ALTER TABLE purchases ADD CONSTRAINT ck_purchases_not_received_and_cancelled
                    CHECK (NOT (is_received AND is_cancelled));
            END IF;
        END $$;
    """))
    await conn.execute(text(
        "ALTER TABLE purchase_items ADD COLUMN IF NOT EXISTS cost_deviation_confirmed BOOLEAN DEFAULT FALSE;"
    ))
    await conn.execute(text(
        "UPDATE purchase_items SET cost_deviation_confirmed = FALSE WHERE cost_deviation_confirmed IS NULL;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchase_items ALTER COLUMN cost_deviation_confirmed SET DEFAULT FALSE;"
    ))
    await conn.execute(text(
        "ALTER TABLE purchase_items ALTER COLUMN cost_deviation_confirmed SET NOT NULL;"
    ))


async def ensure_stock_master_name_constraint(conn: AsyncConnection):
    """Create case-insensitive material uniqueness after checking legacy duplicates."""
    duplicates = (await conn.execute(text(
        "SELECT lower(trim(nama_item)) AS normalized_name, count(*) AS row_count "
        "FROM stock_items GROUP BY lower(trim(nama_item)) HAVING count(*) > 1"
    ))).all()
    if duplicates:
        names = ", ".join(row.normalized_name for row in duplicates)
        raise RuntimeError(f"Duplicate stock master names need reconciliation: {names}")
    await conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_items_normalized_name "
        "ON stock_items (lower(trim(nama_item)))"
    ))
    await conn.execute(text("""
        INSERT INTO stock_movements
            (stock_item_id, movement_type, quantity, unit_cost, reference_type, reference_id, reason)
        SELECT s.id, 'OPENING_BALANCE', s.stok_tersedia, s.harga_per_satuan, 'migration', s.id,
               'Saldo awal saat ledger inventory diaktifkan'
        FROM stock_items s
        WHERE COALESCE(s.stok_tersedia, 0) > 0
          AND NOT EXISTS (SELECT 1 FROM stock_movements m WHERE m.stock_item_id = s.id)
    """))


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

    NOTE: Base.metadata.create_all may have already created ix_users_email as a plain
    non-partial unique index (from Column(email, unique=True, index=True) on the model).
    The bootstrap validator requires it to be a partial unique B-tree index with the predicate
    WHERE email IS NOT NULL.  We therefore check the existing index's predicate and, if it
    lacks one, drop and recreate it as the correct partial form.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(100);"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20);"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);"))

    # Check whether ix_users_email already exists and whether it has the required WHERE predicate.
    index_row = (await conn.execute(text("""
        SELECT idx.relname AS name,
               pg_get_expr(i.indpred, i.indrelid) AS predicate
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_namespace ns  ON ns.oid  = tbl.relnamespace
        WHERE ns.nspname = current_schema()
          AND tbl.relname = 'users'
          AND idx.relname = 'ix_users_email'
    """))).mappings().first()

    if index_row is None:
        # Index does not exist yet — create the correct partial form.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email "
            "ON users (email) WHERE email IS NOT NULL;"
        ))
    else:
        predicate = index_row["predicate"]
        is_valid_predicate = False
        if predicate:
            normalised = predicate.replace('"', "").replace(" ", "").replace("(", "").replace(")", "").lower()
            if normalised == "emailisnotnull":
                is_valid_predicate = True

        if not is_valid_predicate:
            # Drop plain or invalid index and recreate with the required partial predicate.
            await conn.execute(text("DROP INDEX ix_users_email;"))
            await conn.execute(text(
                "CREATE UNIQUE INDEX ix_users_email "
                "ON users (email) WHERE email IS NOT NULL;"
            ))

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
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_expires_at TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_overdue_notified_at TIMESTAMPTZ;"))

    # OrderItem columns & nullable product_id
    await conn.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS custom_product_name VARCHAR(255);"))
    await conn.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS product_name_snapshot VARCHAR(255);"))
    await conn.execute(text("ALTER TABLE order_items ADD COLUMN IF NOT EXISTS unit_price_snapshot NUMERIC(10, 2);"))
    await conn.execute(text("ALTER TABLE order_items ALTER COLUMN product_id DROP NOT NULL;"))

    # Invoice column length
    await conn.execute(text("ALTER TABLE invoices ALTER COLUMN nomor_invoice TYPE VARCHAR(50);"))


async def ensure_customer_columns(conn: AsyncConnection):
    """
    Ensure buyer_id and relevant columns/indices exist on customers table on PostgreSQL.
    """
    if getattr(conn, "dialect", None) is None or getattr(conn.dialect, "name", "") != "postgresql":
        return

    await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS buyer_id INTEGER REFERENCES buyers(id) ON DELETE SET NULL;"))
    await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_buyer_id ON customers (buyer_id) WHERE buyer_id IS NOT NULL;"))

async def ensure_fulfillment_columns(conn: AsyncConnection):
    """
    Ensure fulfillment_date and settlement_due_date columns exist in 'orders' table.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_date TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS settlement_due_date TIMESTAMPTZ;"))


async def ensure_order_accessories_table(conn: AsyncConnection):
    """Create the snapshot table used by seller custom-order accessories."""
    if conn.dialect.name != "postgresql":
        return
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS order_accessories (
            id SERIAL PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            stock_item_id INTEGER NOT NULL REFERENCES stock_items(id),
            stock_item_name VARCHAR(100) NOT NULL,
            unit VARCHAR(20) NOT NULL,
            quantity NUMERIC(10, 4) NOT NULL,
            unit_cost_snapshot NUMERIC(10, 4) NOT NULL,
            subtotal NUMERIC(10, 2) NOT NULL
        )
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_order_accessories_order_id ON order_accessories(order_id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_order_accessories_stock_item_id ON order_accessories(stock_item_id);"))


async def ensure_order_status_enum(conn: AsyncConnection):
    """
    Ensure 'refunded' and 'completed' values exist in PostgreSQL enum 'orderstatusenum'.
    PostgreSQL requires ALTER TYPE ADD VALUE to run outside transaction blocks (autocommit mode).
    """
    if conn.dialect.name != "postgresql":
        return
    try:
        autocommit_conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for val in ["completed", "refunded", "cancelled_settlement_expired"]:
            await autocommit_conn.execute(text(
                f"ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS '{val}';"
            ))
    except Exception:
        # Fallback to independent connection if the passed connection was inside an existing transaction
        try:
            async with conn.engine.connect() as auto_conn:
                autocommit_conn = await auto_conn.execution_options(isolation_level="AUTOCOMMIT")
                for val in ["completed", "refunded", "cancelled_settlement_expired"]:
                    await autocommit_conn.execute(text(
                        f"ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS '{val}';"
                    ))
        except Exception:
            pass


def _payment_order_id_index_is_compatible(index) -> bool:
    """Return whether the named index is a valid unique btree on one plain column."""
    return bool(
        index
        and index.get("relkind") == "i"
        and index.get("table_name") == "payments"
        and index.get("access_method") == "btree"
        and index.get("is_unique")
        and index.get("is_valid")
        and index.get("is_ready")
        and index.get("has_no_predicate")
        and index.get("has_no_expressions")
        and index.get("key_count") == 1
        and index.get("total_count") == 1
        and index.get("first_key") == "pg_order_id"
    )


async def _get_payment_order_id_index(conn: AsyncConnection):
    return (await conn.execute(text("""
        SELECT idx.relkind::text AS relkind,
               tbl.relname AS table_name,
               am.amname AS access_method,
               i.indisunique AS is_unique,
               i.indisvalid AS is_valid,
               i.indisready AS is_ready,
               i.indpred IS NULL AS has_no_predicate,
               i.indexprs IS NULL AS has_no_expressions,
               i.indnkeyatts AS key_count,
               i.indnatts AS total_count,
               CASE WHEN i.indexrelid IS NOT NULL
                    THEN pg_get_indexdef(idx.oid, 1, true)
               END AS first_key
        FROM pg_class idx
        JOIN pg_namespace ns ON ns.oid = idx.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = idx.oid
        LEFT JOIN pg_class tbl ON tbl.oid = i.indrelid
        LEFT JOIN pg_am am ON am.oid = idx.relam
        WHERE ns.nspname = current_schema()
          AND idx.relname = 'uq_payments_pg_order_id'
    """))).mappings().first()


async def ensure_payment_columns(conn: AsyncConnection):
    """
    Ensure payment compatibility columns and the Midtrans order ID index exist
    in the PostgreSQL payments table.
    Backfills existing payments with created_at if settled_at is NULL.
    """
    if conn.dialect.name != "postgresql":
        return

    has_created_at = await conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = 'payments' AND column_name = 'created_at')"
    ))
    if not has_created_at:
        raise RuntimeError(
            "Cannot backfill payments.settled_at: required column "
            "payments.created_at is missing. Restore or reconcile the schema "
            "before running payment schema repair."
        )

    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS notes TEXT;"))
    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;"))
    await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS pg_order_id VARCHAR(100);"))

    order_id_index = await _get_payment_order_id_index(conn)
    if order_id_index is not None and not _payment_order_id_index_is_compatible(order_id_index):
        raise RuntimeError(
            "Index name uq_payments_pg_order_id already exists but is not a valid "
            "unique btree index on payments(pg_order_id). Inspect and reconcile the "
            "index manually; bootstrap will not drop or replace it."
        )

    if order_id_index is None:
        duplicate_rows = (await conn.execute(text(
            "SELECT pg_order_id, count(*) AS cnt FROM payments "
            "WHERE pg_order_id IS NOT NULL GROUP BY pg_order_id HAVING count(*) > 1 "
            "ORDER BY pg_order_id LIMIT 10"
        ))).all()
        if duplicate_rows:
            duplicates = ", ".join(
                f"'{row.pg_order_id}' ({row.cnt}x)" for row in duplicate_rows
            )
            raise RuntimeError(
                "Cannot create unique index uq_payments_pg_order_id: duplicate non-NULL "
                f"payments.pg_order_id values exist: {duplicates}. Reconcile these payment "
                "records manually before retrying; no payment data was changed."
            )

        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_pg_order_id ON payments (pg_order_id);"
        ))
        order_id_index = await _get_payment_order_id_index(conn)
        if not _payment_order_id_index_is_compatible(order_id_index):
            raise RuntimeError(
                "Index uq_payments_pg_order_id was not created with the required "
                "unique btree definition on payments(pg_order_id). Inspect the schema "
                "manually before retrying."
            )

    await conn.execute(text("UPDATE payments SET settled_at = created_at WHERE settled_at IS NULL;"))


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
        # These indexes are part of the supported PostgreSQL schema. Do not
        # silently stamp a database whose required trigram index could not be built.
        await conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} USING gin ({col_name} gin_trgm_ops);"
        ))

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
        await conn.execute(text(
            f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE TEXT;"
        ))

    # 4. Rating precision conversion (DOUBLE PRECISION/FLOAT -> NUMERIC(3, 2))
    await conn.execute(text(
        "ALTER TABLE products ALTER COLUMN rating TYPE NUMERIC(3, 2) USING rating::NUMERIC(3, 2);"
    ))

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
        await conn.execute(text(
            f"UPDATE {table_name} SET {col_name} = COALESCE({col_name}, {default_val}) WHERE {col_name} IS NULL;"
        ))
        await conn.execute(text(
            f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET DEFAULT {default_val};"
        ))
        await conn.execute(text(
            f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET NOT NULL;"
        ))


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


async def ensure_notification_table(conn: AsyncConnection):
    """
    Ensure notifications table exists with proper indices on PostgreSQL.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            recipient_buyer_id INTEGER REFERENCES buyers(id) ON DELETE CASCADE,
            recipient_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            type VARCHAR(50) NOT NULL,
            order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
            actor_type VARCHAR(20),
            actor_id INTEGER,
            metadata_json TEXT,
            read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
    """))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_recipient_buyer_id ON notifications (recipient_buyer_id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_recipient_user_id ON notifications (recipient_user_id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_type ON notifications (type);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_order_id ON notifications (order_id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_read_at ON notifications (read_at);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications (created_at);"))


async def ensure_review_images_table(conn: AsyncConnection):
    """
    Ensure 'review_images' table exists on PostgreSQL database.
    """
    if conn.dialect.name != "postgresql":
        return

    # 1. Create table if not exists
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS review_images (
            id SERIAL PRIMARY KEY,
            review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
            image_url TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """))

    # 2. Indices
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_review_images_id ON review_images (id);"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_review_images_review_id ON review_images (review_id);"))


async def ensure_email_outbox_indexes(conn: AsyncConnection):
    """
    Ensure lease expiry index exists on email_outbox table on PostgreSQL.
    """
    if conn.dialect.name != "postgresql":
        return
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_email_outbox_lease_expiry "
        "ON email_outbox (status, lease_expires_at);"
    ))


async def run_auto_migrations(conn: AsyncConnection):
    """
    Wrapper to run all auto-migrations sequentially on a given connection.
    """
    await ensure_product_columns(conn)
    await ensure_buyer_columns(conn)
    await ensure_stock_item_columns(conn)
    await ensure_purchase_cancellation_columns(conn)
    await ensure_stock_master_name_constraint(conn)
    await ensure_recipe_columns(conn)
    await ensure_otp_columns(conn)
    await ensure_user_columns(conn)
    await ensure_order_columns(conn)
    await ensure_customer_columns(conn)
    await ensure_fulfillment_columns(conn)
    await ensure_order_status_enum(conn)
    await ensure_order_accessories_table(conn)
    await ensure_payment_columns(conn)
    await ensure_review_columns(conn)
    await ensure_trigram_and_schema_optimizations(conn)
    await ensure_product_images_table(conn)
    await ensure_notification_table(conn)
    await ensure_review_images_table(conn)
    await ensure_email_outbox_indexes(conn)
