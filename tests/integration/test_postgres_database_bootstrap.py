"""PostgreSQL tests for fresh schema bootstrap and a data-bearing Alembic upgrade."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Customer, EmailOutbox, Invoice, Order, Payment, StockItem, StockMovement
from app.models.order import InvoiceStatusEnum, MetodePengirimanEnum, OrderStatusEnum
from app.models.payment import PaymentStatusEnum, PaymentTypeEnum
from app.models.stock_item import KategoriEnum, SatuanEnum


def _test_url(env_name: str) -> str:
    value = os.getenv(env_name)
    if not value:
        pytest.skip(f"{env_name} is not configured; PostgreSQL bootstrap/migration test was not run")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+asyncpg" or parsed.host not in {"localhost", "127.0.0.1"}:
        pytest.fail(f"{env_name} must use postgresql+asyncpg on localhost")
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail(f"{env_name} database name must end in _test")
    return value


pytestmark = pytest.mark.asyncio(loop_scope="function")


def _expected_head_revision() -> str:
    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    return ScriptDirectory.from_config(Config(str(config_path))).get_current_head()


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_empty_database_bootstrap_creates_current_schema_and_stamps_head():
    url = _test_url("TEST_POSTGRES_BOOTSTRAP_URL")
    child_env = os.environ.copy()
    child_env.update({
        "DATABASE_URL": url,
        "SECRET_KEY": "local-test-only",
        "SERVICE_API_KEY": "local-test-only",
        "ENVIRONMENT": "test",
    })
    result = subprocess.run(
        [sys.executable, "-m", "app.db_bootstrap", "--confirm-empty"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, "fresh database bootstrap command failed"

    engine = create_async_engine(url)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        payment_columns = await connection.run_sync(
            lambda sync_connection: {column["name"] for column in inspect(sync_connection).get_columns("payments")}
        )
        payment_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("payments")
        )
        outbox_columns = await connection.run_sync(
            lambda sync_connection: {column["name"] for column in inspect(sync_connection).get_columns("email_outbox")}
        )
        outbox_indexes = await connection.run_sync(
            lambda sync_connection: {index["name"] for index in inspect(sync_connection).get_indexes("email_outbox")}
        )
        email_indexes = (await connection.execute(text("""
            SELECT idx.relname AS name, am.amname AS access_method,
                   i.indisunique AS is_unique, i.indisvalid AS is_valid,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate,
                   (SELECT opc.opcname
                    FROM unnest(i.indclass) AS cls(opclass_oid)
                    JOIN pg_opclass opc ON opc.oid = cls.opclass_oid
                    LIMIT 1) AS operator_class,
                   pg_get_indexdef(idx.oid) AS definition
            FROM pg_index i
            JOIN pg_class idx ON idx.oid = i.indexrelid
            JOIN pg_class tbl ON tbl.oid = i.indrelid
            JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
            JOIN pg_am am ON am.oid = idx.relam
            WHERE ns.nspname = current_schema() AND tbl.relname = 'users'
              AND idx.relname IN ('ix_users_email', 'ix_users_email_trgm')
        """))).mappings().all()
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()

    assert {"users", "orders", "payments", "stock_movements", "refund_transactions",
            "email_outbox", "email_outbox_audit"}.issubset(tables)
    assert "pg_order_id" in payment_columns
    payment_order_index = next(
        index for index in payment_indexes if index["name"] == "uq_payments_pg_order_id"
    )
    assert payment_order_index["unique"] is True
    assert payment_order_index["column_names"] == ["pg_order_id"]
    assert {"lease_token", "lease_expires_at"}.issubset(outbox_columns)
    assert "ix_email_outbox_lease_expiry" in outbox_indexes
    email_indexes = {index["name"]: index for index in email_indexes}
    btree = email_indexes["ix_users_email"]
    assert btree["access_method"] == "btree" and btree["is_unique"] and btree["is_valid"]
    assert "email IS NOT NULL" in btree["predicate"]
    assert "(email)" in btree["definition"]
    trigram = email_indexes["ix_users_email_trgm"]
    assert trigram["access_method"] == "gin" and not trigram["is_unique"] and trigram["is_valid"]
    assert trigram["predicate"] is None
    assert trigram["operator_class"] == "gin_trgm_ops"
    assert "email gin_trgm_ops" in trigram["definition"]
    assert revision == _expected_head_revision()


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_upgrade_from_existing_revision_preserves_payment_stock_and_outbox_rows():
    url = _test_url("TEST_POSTGRES_UPGRADE_URL")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        customer = Customer(nama="Upgrade fixture", nomor_wa="+628123450000")
        db.add(customer)
        await db.flush()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                      metode_pengiriman=MetodePengirimanEnum.pickup, total_harga_pesanan=100000)
        db.add(order)
        await db.flush()
        invoice = Invoice(order_id=order.id, nomor_invoice=f"UPGRADE-{order.id}", total_tagihan=100000,
                          status=InvoiceStatusEnum.partial)
        db.add(invoice)
        await db.flush()
        payment = Payment(invoice_id=invoice.id, pg_transaction_id="upgrade-pending-txn",
                          pg_order_id="upgrade-order-ref", jumlah_bayar=50000, payment_method="qris",
                          payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.dp)
        stock = StockItem(nama_item="Upgrade flour", satuan=SatuanEnum.gram,
                          kategori=KategoriEnum.bahan_baku, harga_per_satuan=10, stok_tersedia=321,
                          alert_min_stok=0, version=0)
        db.add_all([payment, stock])
        await db.flush()
        movement = StockMovement(stock_item_id=stock.id, movement_type="OPENING_BALANCE", quantity=321,
                                 unit_cost=10, reference_type="test", reference_id=stock.id)
        event = EmailOutbox(event_key="upgrade-existing-outbox", recipient="buyer@example.test",
                            event_type="payment_status_updated", payload_json=json.dumps({"payment_status": "Pending"}),
                            status="pending", attempts=1)
        db.add_all([movement, event])
        await db.commit()
        payment_id, stock_id, movement_id, event_id = payment.id, stock.id, movement.id, event.id

    # Model a database already tracked at 20261013_01, before the later revisions.
    # This is a disposable *_test database; it does not run Alembic downgrade.
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE email_outbox_audit"))
        await connection.execute(text("DROP INDEX ix_email_outbox_lease_expiry"))
        await connection.execute(text("ALTER TABLE email_outbox DROP COLUMN lease_expires_at"))
        await connection.execute(text("ALTER TABLE email_outbox DROP COLUMN lease_token"))
        await connection.execute(text("DROP INDEX uq_payments_pg_order_id"))
        await connection.execute(text("ALTER TABLE payments DROP COLUMN pg_order_id"))
        # Base.metadata.create_all above reflects the current models. Remove objects
        # introduced after 20261013_01 so this fixture represents the older schema
        # that Alembic is about to upgrade.
        await connection.execute(text(
            "ALTER TABLE purchases DROP CONSTRAINT IF EXISTS ck_purchases_not_received_and_cancelled"
        ))
        await connection.execute(text("ALTER TABLE purchase_items DROP COLUMN IF EXISTS cost_deviation_confirmed"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancelled_by"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancellation_reason"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancelled_at"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS is_cancelled"))
        await connection.execute(text("DROP TABLE IF EXISTS order_accessories"))
        await connection.execute(text("ALTER TABLE order_items DROP COLUMN IF EXISTS unit_price_snapshot"))
        await connection.execute(text("ALTER TABLE order_items DROP COLUMN IF EXISTS product_name_snapshot"))
        # These columns and the settings table are part of the current ORM metadata,
        # but did not exist at the revision being upgraded from. Keep the database
        # aligned with 20261013_01 so the lifecycle migration exercises a real upgrade.
        await connection.execute(text("DROP INDEX IF EXISTS ix_orders_payment_expires_at"))
        await connection.execute(text("ALTER TABLE orders DROP COLUMN IF EXISTS payment_expires_at"))
        await connection.execute(text("ALTER TABLE orders DROP COLUMN IF EXISTS fulfillment_overdue_notified_at"))
        await connection.execute(text("DROP TABLE IF EXISTS email_sender_settings"))
        await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('20261013_01')"))

    child_env = os.environ.copy()
    child_env.update({
        "DATABASE_URL": url,
        "SECRET_KEY": "local-test-only",
        "SERVICE_API_KEY": "local-test-only",
        "ENVIRONMENT": "test",
    })
    result = subprocess.run(
        [sys.executable, "-c", "from alembic.config import main; main()", "upgrade", "head"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, "Alembic upgrade from the recorded revision failed"

    async with factory() as db:
        payment = await db.get(Payment, payment_id)
        stock = await db.get(StockItem, stock_id)
        movement = await db.get(StockMovement, movement_id)
        event = await db.get(EmailOutbox, event_id)
        revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
        assert payment.payment_status == PaymentStatusEnum.pending
        assert payment.pg_transaction_id == "upgrade-pending-txn"
        assert payment.pg_order_id is None
        assert stock.stok_tersedia == 321
        assert movement.quantity == 321
        assert event.status == "pending"
        assert event.attempts == 1
        assert event.payload_json == json.dumps({"payment_status": "Pending"})
        assert revision == _expected_head_revision()

    await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_adoption_preflight_blocks_invalid_database():
    url = _test_url("TEST_POSTGRES_UPGRADE_URL")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.run_sync(Base.metadata.create_all)
        # Introduce duplicate stock item to violate preflight
        await connection.execute(text(
            "INSERT INTO stock_items (nama_item, satuan, kategori, harga_per_satuan, stok_tersedia, alert_min_stok, version) "
            "VALUES ('Tepung Terigu', 'gram', 'bahan_baku', 10, 100, 0, 0)"
        ))
        await connection.execute(text(
            "INSERT INTO stock_items (nama_item, satuan, kategori, harga_per_satuan, stok_tersedia, alert_min_stok, version) "
            "VALUES ('tepung terigu ', 'gram', 'bahan_baku', 10, 100, 0, 0)"
        ))

    child_env = os.environ.copy()
    child_env.update({
        "DATABASE_URL": url,
        "SECRET_KEY": "local-test-only",
        "SERVICE_API_KEY": "local-test-only",
        "ENVIRONMENT": "test",
    })
    res = subprocess.run(
        [sys.executable, "-m", "app.db_preflight"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert res.returncode == 1, "Preflight must exit 1 when duplicate stock names exist"
    assert "Duplicate stock master names must be reconciled" in res.stdout
    await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(("case", "expected_error"), [
    ("refund_objects", "Do not stamp 20261005_01"),
    ("refund_enum_only", "Do not stamp 20261005_01"),
    ("missing_stock_index", "does not match the required valid UNIQUE btree expression"),
    ("nonunique_stock_index", "does not match the required valid UNIQUE btree expression"),
    ("wrong_stock_index_expression", "does not match the required valid UNIQUE btree expression"),
    ("duplicate_opening", "OPENING_BALANCE movements; exactly one is allowed"),
    ("opening_quantity_mismatch", "ledger quantity"),
    ("opening_cost_mismatch", "reconstructed ledger unit cost"),
    ("negative_opening", "negative OPENING_BALANCE quantity"),
    ("negative_unit_cost", "negative or non-finite stock movement cost/quantity"),
    ("negative_stock", "negative or non-finite master stock/price"),
    ("missing_opening", "does not have exactly one OPENING_BALANCE"),
])
async def test_postgres_preflight_invalid_state_fails_before_stamp(case, expected_error):
    url = _test_url("TEST_POSTGRES_UPGRADE_URL")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        # alembic_version is not in Base.metadata so drop_all leaves it behind when a
        # previous test (e.g. the upgrade test) stamped the DB.  Drop it explicitly so
        # the preflight assertion "failed run must not create alembic_version" is reliable.
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.run_sync(Base.metadata.create_all)
        if case != "refund_objects":
            await connection.execute(text("DROP TABLE refund_transactions"))
        if case not in {"refund_objects", "refund_enum_only"}:
            await connection.execute(text("DROP TYPE refundstatusenum"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        if case == "nonunique_stock_index":
            await connection.execute(text(
                "CREATE INDEX uq_stock_items_normalized_name "
                "ON stock_items (lower(trim(nama_item)))"
            ))
        elif case == "wrong_stock_index_expression":
            await connection.execute(text(
                "CREATE UNIQUE INDEX uq_stock_items_normalized_name "
                "ON stock_items (lower(nama_item))"
            ))
        elif case not in {"missing_stock_index"}:
            await connection.execute(text(
                "CREATE UNIQUE INDEX uq_stock_items_normalized_name "
                "ON stock_items (lower(trim(nama_item)))"
            ))

        stock_quantity = -1 if case == "negative_stock" else 10
        await connection.execute(text(
            "INSERT INTO stock_items (nama_item, satuan, kategori, harga_per_satuan, stok_tersedia, alert_min_stok, version) "
            f"VALUES ('Preflight flour', 'gram', 'bahan_baku', 2.0000, {stock_quantity}, 0, 0)"
        ))
        if case != "missing_opening":
            opening_quantity = {
                "opening_quantity_mismatch": 9,
                "negative_opening": -10,
            }.get(case, stock_quantity)
            opening_cost = {
                "opening_cost_mismatch": 3,
                "negative_unit_cost": -2,
            }.get(case, 2)
            await connection.execute(text(
                "INSERT INTO stock_movements (stock_item_id, movement_type, quantity, unit_cost) "
                f"VALUES (1, 'OPENING_BALANCE', {opening_quantity}, {opening_cost})"
            ))
        if case == "duplicate_opening":
            await connection.execute(text(
                "INSERT INTO stock_movements (stock_item_id, movement_type, quantity, unit_cost) "
                "VALUES (1, 'OPENING_BALANCE', 10, 2)"
            ))

    child_env = os.environ.copy()
    child_env.update({
        "DATABASE_URL": url,
        "SECRET_KEY": "local-test-only",
        "SERVICE_API_KEY": "local-test-only",
        "ENVIRONMENT": "test",
    })
    result = subprocess.run(
        [sys.executable, "-m", "app.db_preflight", "--stamp"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert expected_error in result.stdout
    async with engine.connect() as connection:
        version_table_exists = await connection.scalar(text(
            "SELECT to_regclass('alembic_version') IS NOT NULL"
        ))
    assert not version_table_exists, "failed preflight must not stamp or create alembic_version"
    await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_adopts_verified_refund_revision_then_upgrades_to_head():
    """A schema at 20261008_01 can be adopted without replaying that revision."""
    url = _test_url("TEST_POSTGRES_UPGRADE_URL")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await connection.run_sync(Base.metadata.create_all)
        # Base.metadata reflects the current schema. Revert only later-revision
        # objects to represent the database immediately after 20261008_01.
        await connection.execute(text("DROP TABLE email_outbox_audit"))
        await connection.execute(text("DROP TABLE email_outbox"))
        await connection.execute(text("DROP TABLE order_accessories"))
        await connection.execute(text(
            "ALTER TABLE purchases DROP CONSTRAINT IF EXISTS ck_purchases_not_received_and_cancelled"
        ))
        await connection.execute(text("ALTER TABLE purchase_items DROP COLUMN IF EXISTS cost_deviation_confirmed"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancelled_by"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancellation_reason"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS cancelled_at"))
        await connection.execute(text("ALTER TABLE purchases DROP COLUMN IF EXISTS is_cancelled"))
        await connection.execute(text("ALTER TABLE payments DROP COLUMN pg_order_id"))
        await connection.execute(text("ALTER TABLE order_items DROP COLUMN unit_price_snapshot"))
        await connection.execute(text("ALTER TABLE order_items DROP COLUMN product_name_snapshot"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await connection.execute(text(
            "CREATE UNIQUE INDEX uq_stock_items_normalized_name "
            "ON stock_items (lower(trim(nama_item)))"
        ))

    child_env = os.environ.copy()
    child_env.update({
        "DATABASE_URL": url,
        "SECRET_KEY": "local-test-only",
        "SERVICE_API_KEY": "local-test-only",
        "ENVIRONMENT": "test",
    })
    preflight = subprocess.run(
        [sys.executable, "-m", "app.db_preflight", "--target-revision", "20261008_01"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr

    stamp = subprocess.run(
        [sys.executable, "-m", "app.db_preflight", "--target-revision", "20261008_01", "--stamp"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    upgrade = subprocess.run(
        [sys.executable, "-c", "from alembic.config import main; main()", "upgrade", "head"],
        cwd=os.getcwd(), env=child_env, capture_output=True, text=True, check=False,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    async with engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        payment_columns = {column["name"] for column in await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("payments")
        )}
        order_item_columns = {column["name"] for column in await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns("order_items")
        )}
        tables = set(await connection.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    assert revision == _expected_head_revision()
    assert "pg_order_id" in payment_columns
    assert {"product_name_snapshot", "unit_price_snapshot"} <= order_item_columns
    assert {"email_outbox", "email_outbox_audit", "order_accessories"} <= tables
    await engine.dispose()

