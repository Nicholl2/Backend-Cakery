"""Destructive PostgreSQL tests for the read-only drift audit.

Run only against a disposable database whose name ends in
``_reconcile_test`` by setting RECONCILE_TEST_DATABASE_URL and the explicit
RECONCILE_TEST_DB_ALLOW_RESET=1 opt-in. Each test resets that database's
public schema; no application/development URL is accepted.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import db_reconcile
from app.db_reconcile import audit_database


DATABASE_URL = os.getenv("RECONCILE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires isolated RECONCILE_TEST_DATABASE_URL")


def _assert_disposable_url(url: str) -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql", "reconciliation integration tests require PostgreSQL"
    assert parsed.database and parsed.database.endswith("_reconcile_test"), (
        "refusing destructive test setup unless database name ends with _reconcile_test"
    )
    assert parsed.host in {"localhost", "127.0.0.1", "::1", None}, (
        "refusing reconciliation integration tests against a non-local database"
    )
    assert os.getenv("RECONCILE_TEST_DB_ALLOW_RESET") == "1", (
        "set RECONCILE_TEST_DB_ALLOW_RESET=1 only for a newly created disposable database"
    )


async def _prepare_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
        await connection.execute(text("CREATE EXTENSION pg_trgm"))
        for ddl in (
            "CREATE TYPE paymentstatusenum AS ENUM ('success','pending','failed','refunded')",
            "CREATE TYPE paymenttypeenum AS ENUM ('dp','final')",
            "CREATE TYPE refundstatusenum AS ENUM ('pending','completed','failed')",
        ):
            await connection.execute(text(ddl))
        core = {
            "users", "roles", "categories", "products", "product_images", "recipes",
            "price_histories", "suppliers", "purchases", "purchase_items", "faq_items",
            "expenses", "customers", "invoices", "reviews", "review_images", "buyers",
            "otp_codes", "notifications",
        }
        for table in sorted(core):
            await connection.execute(text(f"CREATE TABLE public.{table} (id integer PRIMARY KEY)"))
        await connection.execute(text("""
            CREATE TABLE public.orders (
                id integer PRIMARY KEY, fulfillment_date timestamptz,
                settlement_due_date timestamptz, notes text, due_date timestamptz
            )
        """))
        await connection.execute(text("""
            CREATE TABLE public.stock_items (
                id integer PRIMARY KEY, nama_item varchar(100) NOT NULL,
                harga_per_satuan numeric(10,4) NOT NULL,
                stok_tersedia numeric(10,2) NOT NULL,
                alert_min_stok numeric(10,2) NOT NULL, version integer NOT NULL
            )
        """))
        await connection.execute(text(
            "CREATE UNIQUE INDEX uq_stock_items_normalized_name ON public.stock_items "
            "(lower(trim(nama_item)))"
        ))
        await connection.execute(text("""
            CREATE TABLE public.stock_movements (
                id integer PRIMARY KEY, stock_item_id integer NOT NULL REFERENCES stock_items(id),
                movement_type varchar(32) NOT NULL, quantity numeric(10,4) NOT NULL,
                unit_cost numeric(10,4) NOT NULL DEFAULT 0, created_at timestamptz DEFAULT now()
            )
        """))
        await connection.execute(text("""
            CREATE TABLE public.payments (
                id serial PRIMARY KEY, invoice_id integer NOT NULL REFERENCES invoices(id),
                pg_transaction_id varchar(100), jumlah_bayar numeric(10,2) NOT NULL,
                payment_method varchar(50) NOT NULL, notes text,
                verified_by integer REFERENCES users(id),
                payment_status paymentstatusenum NOT NULL, payment_type paymenttypeenum NOT NULL,
                created_at timestamptz DEFAULT now(), settled_at timestamptz, updated_at timestamptz,
                va_number varchar(50), qris_url text
            )
        """))
        await connection.execute(text("CREATE INDEX ix_payments_id ON public.payments(id)"))
        await connection.execute(text("CREATE INDEX ix_payments_invoice_id ON public.payments(invoice_id)"))
        await connection.execute(text("""
            CREATE TABLE public.refund_transactions (
                id serial PRIMARY KEY,
                payment_id integer NOT NULL REFERENCES payments(id),
                refund_amount numeric(10,2) NOT NULL,
                status refundstatusenum NOT NULL DEFAULT 'pending',
                reason varchar(500) NOT NULL,
                gateway_ref varchar(100) UNIQUE,
                initiated_by integer REFERENCES users(id),
                initiated_source varchar(32) NOT NULL DEFAULT 'seller',
                created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
            )
        """))
        await connection.execute(text("CREATE INDEX ix_refund_transactions_id ON public.refund_transactions(id)"))
        await connection.execute(text(
            "CREATE INDEX ix_refund_transactions_payment_id ON public.refund_transactions(payment_id)"
        ))
        await connection.execute(text("""
            CREATE TABLE public.order_items (
                id integer PRIMARY KEY, product_name_snapshot varchar(255),
                unit_price_snapshot numeric(10,2), hpp_snapshot numeric(10,2) NOT NULL
            )
        """))
        await connection.execute(text("""
            CREATE TABLE public.order_accessories (
                id serial PRIMARY KEY,
                order_id integer NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                stock_item_id integer NOT NULL REFERENCES stock_items(id),
                stock_item_name varchar(100) NOT NULL, unit varchar(20) NOT NULL,
                quantity numeric(10,4) NOT NULL, unit_cost_snapshot numeric(10,4) NOT NULL,
                subtotal numeric(10,2) NOT NULL
            )
        """))
        await connection.execute(text(
            "CREATE INDEX ix_order_accessories_order_id ON public.order_accessories(order_id)"
        ))
        await connection.execute(text(
            "CREATE INDEX ix_order_accessories_stock_item_id ON public.order_accessories(stock_item_id)"
        ))


@pytest_asyncio.fixture
async def reconcile_engine():
    assert DATABASE_URL
    _assert_disposable_url(DATABASE_URL)
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    try:
        await _prepare_schema(engine)
        yield engine
    finally:
        await engine.dispose()


async def test_audit_accepts_exact_synthetic_mixed_baseline(reconcile_engine):
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_TARGETED_CHECKS_PASSED_DBA_REVIEW_REQUIRED", report.findings
    assert not report.findings
    assert report.checkpoints["payments_rows"] == 0
    assert "payments_with_completed_refunds" in report.checkpoints
    async with reconcile_engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('public.alembic_version') IS NULL"))
        assert await connection.scalar(text("SELECT count(*) FROM public.payments")) == 0


async def test_audit_rejects_partial_outbox(reconcile_engine):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE public.email_outbox (id integer)"))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert any("Outbox objects" in finding for finding in report.findings)


@pytest.mark.parametrize(
    "replacement",
    [
        "CREATE INDEX ix_order_accessories_order_id ON public.order_accessories(stock_item_id)",
        "CREATE INDEX ix_order_accessories_order_id ON public.order_accessories(order_id) WHERE id > 0",
        "CREATE INDEX ix_order_accessories_order_id ON public.order_accessories USING hash (order_id)",
        "CREATE UNIQUE INDEX ix_order_accessories_order_id ON public.order_accessories(order_id)",
        "CREATE INDEX ix_order_accessories_order_id ON public.order_accessories(order_id, stock_item_id)",
    ],
)
async def test_audit_rejects_wrong_accessory_index_definition(reconcile_engine, replacement):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("DROP INDEX public.ix_order_accessories_order_id"))
        await connection.execute(text(replacement))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert any("ix_order_accessories_order_id" in finding for finding in report.findings)


async def test_audit_rejects_refund_amount_inconsistent_with_refunded_payment(reconcile_engine):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("INSERT INTO public.users(id) VALUES (1)"))
        await connection.execute(text("INSERT INTO public.invoices(id) VALUES (1)"))
        await connection.execute(text("""
            INSERT INTO public.payments(id, invoice_id, jumlah_bayar, payment_method,
                payment_status, payment_type)
            VALUES (1, 1, 100, 'test', 'refunded', 'final')
        """))
        await connection.execute(text("""
            INSERT INTO public.refund_transactions(payment_id, refund_amount, status, reason)
            VALUES (1, 99, 'completed', 'synthetic test')
        """))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert report.checkpoints["refunded_amount_mismatches"] == 1


async def test_audit_rejects_existing_revision_history(reconcile_engine):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO public.alembic_version VALUES ('20261008_01')"))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert any("revision history already exists" in finding for finding in report.findings)


async def test_audit_rejects_default_using_a_different_sequence(reconcile_engine):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("CREATE SEQUENCE public.unrelated_payment_sequence"))
        await connection.execute(text(
            "ALTER TABLE public.payments ALTER COLUMN id "
            "SET DEFAULT nextval('public.unrelated_payment_sequence'::regclass)"
        ))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert any("payments.id does not use its owned integer serial sequence" in finding for finding in report.findings)


async def test_audit_rejects_sequence_behind_existing_payment_ids(reconcile_engine):
    async with reconcile_engine.begin() as connection:
        await connection.execute(text("INSERT INTO public.invoices(id) VALUES (1)"))
        await connection.execute(text("""
            INSERT INTO public.payments(id, invoice_id, jumlah_bayar, payment_method,
                payment_status, payment_type)
            VALUES (10, 1, 100, 'test', 'success', 'final')
        """))
        await connection.execute(text("SELECT setval('public.payments_id_seq', 1, false)"))
    report = await audit_database(reconcile_engine)
    assert report.result == "AUDIT_BLOCKED"
    assert any("sequence would next produce" in finding for finding in report.findings)


async def test_audit_transaction_is_read_only_and_closes_after_error(reconcile_engine, monkeypatch):
    async def attempt_write(connection, tables, findings):
        await connection.execute(text("INSERT INTO public.users(id) VALUES (-1)"))
        return True

    monkeypatch.setattr(db_reconcile, "_check_refund_objects", attempt_write)
    with pytest.raises(DBAPIError):
        await audit_database(reconcile_engine)
    async with reconcile_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM public.users WHERE id=-1")) == 0
