"""PostgreSQL-only order concurrency and idempotency checks."""
import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.customer import Customer
from app.models.order import Invoice, InvoiceStatusEnum, MetodePengirimanEnum, Order, OrderStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem
from app.services.order_service import create_new_order
from app.services.order_service import cancel_order_by_customer
from app.services.payment_service import process_midtrans_webhook
from app.services.payment_service import check_expired_settlements
from app.core.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest.fixture(scope="function")
def pg_url():
    value = os.getenv("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured; PostgreSQL order concurrency checks were not run")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+asyncpg" or parsed.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("TEST_POSTGRES_URL must use postgresql+asyncpg on localhost")
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("TEST_POSTGRES_URL database name must end in _test; refusing to touch a non-test database")
    return value


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def pg_sessions(pg_url):
    engine = create_async_engine(pg_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        customers = [Customer(nama=f"Concurrent buyer {n}", nomor_wa=f"+62812345000{n}") for n in (1, 2)]
        stock = StockItem(nama_item="Concurrent flour", satuan=SatuanEnum.gram,
                          kategori=KategoriEnum.bahan_baku, harga_per_satuan=1,
                          stok_tersedia=10, version=0)
        product = Product(nama_produk="Concurrent cake", harga_jual=10000, hpp_total=1000,
                          is_active=True, is_available=True, minimum_order=1,
                          slug="postgres-concurrent-cake")
        db.add_all([*customers, stock, product])
        await db.flush()
        db.add(Recipe(product_id=product.id, stock_item_id=stock.id, jumlah_dibutuhkan=10))
        await db.commit()
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _create(factory, customer_id: int, key: str):
    async with factory() as db:
        return await create_new_order(
            db, customer_id=customer_id,
            items=[{"product_id": 1, "jumlah": 1}],
            metode_pengiriman="pickup", email_seller_on_commit=False,
            idempotency_key=key,
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_same_key_returns_one_order(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.notification_service.notify_order_created", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    barrier = asyncio.Barrier(2)

    async def attempt():
        await barrier.wait()
        return await _create(pg_sessions, 1, "same-concurrent-key")

    first, second = await asyncio.gather(attempt(), attempt())
    assert first.id == second.id
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count(Order.id))) == 1
        assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_customers_cannot_oversell_locked_stock(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.notification_service.notify_order_created", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    barrier = asyncio.Barrier(2)

    async def attempt(customer_id):
        await barrier.wait()
        try:
            await _create(pg_sessions, customer_id, f"customer-key-{customer_id}")
            return "created"
        except HTTPException as error:
            return error.status_code

    outcomes = await asyncio.gather(attempt(1), attempt(2))
    assert outcomes.count("created") == 1
    assert outcomes.count(400) == 1
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count(Order.id))) == 1
        assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_payment_settlement_serializes_with_cancellation(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.chatbot_notify.notify_payment_status", noop)
    monkeypatch.setattr("app.services.email_outbox_service.enqueue_order_payment_event", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    key = "postgres-order-race-test-key"
    monkeypatch.setattr(settings, "midtrans_server_key", key)
    async with pg_sessions() as db:
        customer = (await db.execute(select(Customer).order_by(Customer.id))).scalars().first()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                      metode_pengiriman=MetodePengirimanEnum.pickup,
                      total_harga_pesanan=Decimal("10000"))
        db.add(order)
        await db.flush()
        invoice = Invoice(order_id=order.id, nomor_invoice=f"INV-RACE-{order.id}",
                          total_tagihan=Decimal("10000"), status=InvoiceStatusEnum.unpaid)
        db.add(invoice)
        await db.flush()
        payment = Payment(invoice_id=invoice.id, pg_order_id=f"INV-RACE-{order.id}-PAY-1-ABCD",
                          pg_transaction_id="race-transaction", jumlah_bayar=Decimal("5000"),
                          payment_method="qris", payment_status=PaymentStatusEnum.pending,
                          payment_type=PaymentTypeEnum.dp)
        db.add(payment)
        await db.commit()
        order_id = order.id
        pg_order_id = payment.pg_order_id

    gross_amount = "5000.00"
    status_code = "200"
    signature = hashlib.sha512(
        f"{pg_order_id}{status_code}{gross_amount}{key}".encode()
    ).hexdigest()
    payload = {"order_id": pg_order_id, "status_code": status_code,
               "gross_amount": gross_amount, "transaction_status": "settlement",
               "transaction_id": "race-transaction", "signature_key": signature}

    async def settle():
        async with pg_sessions() as db:
            return await process_midtrans_webhook(db, payload)

    async def cancel():
        async with pg_sessions() as db:
            try:
                return await cancel_order_by_customer(db, order_id)
            except HTTPException as exc:
                return exc.status_code

    settled, cancelled = await asyncio.gather(settle(), cancel())
    assert settled["status"] == "success"
    assert cancelled == 409
    async with pg_sessions() as db:
        persisted_order = await db.get(Order, order_id)
        persisted_payment = (await db.execute(select(Payment).where(Payment.pg_order_id == pg_order_id))).scalar_one()
        assert persisted_order.status == OrderStatusEnum.pending
        assert persisted_payment.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_settlement_does_not_expire_order_with_stale_partial_invoice(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.chatbot_notify.notify_payment_status", noop)
    monkeypatch.setattr("app.services.email_outbox_service.enqueue_order_payment_event", noop)
    monkeypatch.setattr("app.services.email_outbox_service.enqueue_order_status_for_buyer", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    key = "postgres-settlement-expiry-key"
    monkeypatch.setattr(settings, "midtrans_server_key", key)
    async with pg_sessions() as db:
        customer = (await db.execute(select(Customer).order_by(Customer.id))).scalars().first()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.in_process,
                      metode_pengiriman=MetodePengirimanEnum.pickup,
                      total_harga_pesanan=Decimal("10000"),
                      settlement_due_date=datetime.now(timezone.utc) - timedelta(days=1))
        db.add(order)
        await db.flush()
        invoice = Invoice(order_id=order.id, nomor_invoice=f"INV-EXPIRY-RACE-{order.id}",
                          total_tagihan=Decimal("10000"), status=InvoiceStatusEnum.partial)
        db.add(invoice)
        await db.flush()
        dp = Payment(invoice_id=invoice.id, pg_order_id=None, pg_transaction_id="expiry-dp",
                     jumlah_bayar=Decimal("5000"), payment_method="CASH",
                     payment_status=PaymentStatusEnum.success, payment_type=PaymentTypeEnum.dp)
        final = Payment(invoice_id=invoice.id, pg_order_id=f"INV-EXPIRY-RACE-{order.id}-PAY-1-ABCD",
                        pg_transaction_id="expiry-final", jumlah_bayar=Decimal("5000"),
                        payment_method="qris", payment_status=PaymentStatusEnum.pending,
                        payment_type=PaymentTypeEnum.final)
        db.add_all([dp, final])
        await db.commit()
        order_id = order.id
        pg_order_id = final.pg_order_id

    gross_amount = "5000.00"
    status_code = "200"
    signature = hashlib.sha512(
        f"{pg_order_id}{status_code}{gross_amount}{key}".encode()
    ).hexdigest()
    payload = {"order_id": pg_order_id, "status_code": status_code,
               "gross_amount": gross_amount, "transaction_status": "settlement",
               "transaction_id": "expiry-final", "signature_key": signature}

    async def settle():
        async with pg_sessions() as db:
            return await process_midtrans_webhook(db, payload)

    async def expire():
        async with pg_sessions() as db:
            return await check_expired_settlements(db)

    settled, _ = await asyncio.gather(settle(), expire())
    assert settled["status"] == "success"
    async with pg_sessions() as db:
        persisted_order = await db.get(Order, order_id)
        persisted_invoice = (await db.execute(select(Invoice).where(Invoice.order_id == order_id))).scalar_one()
        assert persisted_order.status != OrderStatusEnum.cancelled_settlement_expired
        assert persisted_invoice.status == InvoiceStatusEnum.paid
