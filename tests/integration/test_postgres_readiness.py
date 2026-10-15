"""PostgreSQL-only readiness checks; point TEST_POSTGRES_URL at a disposable *_test DB."""
import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base
from app.models import Buyer, Customer, EmailOutbox, Invoice, Order, Payment, Role, User, Product, Recipe
from app.models.order import InvoiceStatusEnum, MetodePengirimanEnum, OrderStatusEnum
from app.models.payment import PaymentStatusEnum, PaymentTypeEnum
from app.schemas.user import UserBootstrap
from app.services.email_outbox_service import enqueue_email
from app.services.payment_service import (
    _apply_transaction_status,
    process_midtrans_webhook,
    refresh_if_pending,
)
from app.services.order_service import cancel_order_by_customer, create_custom_order
from app.schemas.order import CustomOrderCreate
from app.models.stock_item import StockItem, StockMovement, KategoriEnum, SatuanEnum
from app.services.user_service import bootstrap_owner

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest.fixture(scope="function")
def pg_url():
    value = os.getenv("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured; PostgreSQL integration checks were not run")
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
    try:
        yield factory
    finally:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        finally:
            await engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_owner_bootstrap_creates_exactly_one_owner(pg_sessions):
    barrier = asyncio.Barrier(2)

    async def attempt(username: str) -> bool:
        async with pg_sessions() as db:
            await barrier.wait()
            try:
                await bootstrap_owner(db, UserBootstrap(
                    username=username,
                    password="StrongPass123!",
                    nomor_wa_admin="+628123456789",
                ))
                return True
            except HTTPException as exc:
                assert exc.status_code == 400
                return False

    outcomes = await asyncio.gather(attempt("owner_alpha"), attempt("owner_beta"))
    async with pg_sessions() as db:
        assert sum(outcomes) == 1
        assert await db.scalar(select(func.count(User.id))) == 1
        assert await db.scalar(select(func.count(Role.id))) == 3


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_outbox_claim_sends_one_live_lease(pg_sessions, monkeypatch):
    async with pg_sessions() as db:
        await enqueue_email(db, event_key="pg-concurrent-claim", recipient="buyer@example.test",
                            event_type="order_status_updated", payload={"status": "ready"})
        await db.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    sent_ids: list[int] = []

    async def held_send(event):
        sent_ids.append(event.id)
        started.set()
        await release.wait()

    monkeypatch.setattr("app.services.email_worker._send", held_send)

    async def first_worker():
        async with pg_sessions() as db:
            from app.services.email_worker import process_one_email
            return await process_one_email(db)

    first = asyncio.create_task(first_worker())
    await asyncio.wait_for(started.wait(), timeout=5)
    async with pg_sessions() as second_db:
        from app.services.email_worker import process_one_email
        second_result = await process_one_email(second_db)
    release.set()
    first_result = await first

    async with pg_sessions() as db:
        event = (await db.execute(select(EmailOutbox))).scalar_one()
        assert first_result is True
        assert second_result is False
        assert sent_ids == [event.id]
        assert event.status == "sent"
        assert event.attempts == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_order_cancellation_reverses_stock_once(pg_sessions):
    async with pg_sessions() as db:
        customer = Customer(nama="Cancel race", nomor_wa="628123456700")
        db.add(customer)
        await db.flush()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                      metode_pengiriman=MetodePengirimanEnum.pickup,
                      total_harga_pesanan=Decimal("10000"))
        db.add(order)
        await db.flush()
        db.add(Invoice(order_id=order.id, nomor_invoice="INV-CANCEL-RACE",
                       total_tagihan=Decimal("10000"), status=InvoiceStatusEnum.unpaid))
        stock = StockItem(nama_item="Cancel race stock", satuan=SatuanEnum.gram,
                          kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
                          stok_tersedia=Decimal("9"), version=1)
        db.add(stock)
        await db.flush()
        db.add(StockMovement(stock_item_id=stock.id, movement_type="ORDER",
                             quantity=Decimal("-1"), reference_type="order", reference_id=order.id,
                             reason="test order consumption"))
        order_id, stock_id = order.id, stock.id
        await db.commit()

    barrier = asyncio.Barrier(2)

    async def cancel_once():
        async with pg_sessions() as db:
            await barrier.wait()
            try:
                await cancel_order_by_customer(db, order_id)
                return "cancelled"
            except HTTPException as exc:
                assert exc.status_code == 409
                return "already_cancelled"

    outcomes = await asyncio.gather(cancel_once(), cancel_once())
    async with pg_sessions() as db:
        stock = await db.get(StockItem, stock_id)
        reversals = await db.scalar(select(func.count(StockMovement.id)).where(
            StockMovement.reference_type == "order",
            StockMovement.reference_id == order_id,
            StockMovement.movement_type == "REVERSAL",
        ))
        assert sorted(outcomes) == ["already_cancelled", "cancelled"]
        assert stock.stok_tersedia == Decimal("10.0000")
        assert reversals == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_custom_orders_cannot_oversell_recipe_or_accessory_stock(pg_sessions):
    async with pg_sessions() as db:
        flour = StockItem(nama_item="Custom race flour", satuan=SatuanEnum.gram,
                          kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("0.01"),
                          stok_tersedia=Decimal("4"), version=0)
        candle = StockItem(nama_item="Custom race candle", satuan=SatuanEnum.pcs,
                           kategori=KategoriEnum.kemasan, harga_per_satuan=Decimal("100"),
                           stok_tersedia=Decimal("2"), version=0)
        product = Product(nama_produk="Custom race cake", harga_jual=Decimal("10000"),
                          hpp_total=Decimal("1000"), is_active=True, is_available=True,
                          minimum_order=1)
        db.add_all([flour, candle, product])
        await db.flush()
        db.add(Recipe(product_id=product.id, stock_item_id=flour.id,
                      jumlah_dibutuhkan=Decimal("4"), unit="gram"))
        product_id, flour_id, candle_id = product.id, flour.id, candle.id
        await db.commit()

    barrier = asyncio.Barrier(2)
    future = datetime.now(timezone.utc) + timedelta(days=2)

    async def create_for(customer_no: int):
        async with pg_sessions() as db:
            await barrier.wait()
            payload = CustomOrderCreate(
                customer_name=f"Race Buyer {customer_no}",
                customer_phone=f"+6281234500{customer_no:02d}",
                fulfillment_date=future,
                items=[{"product_id": product_id, "qty": 1}],
                accessories=[{"stock_item_id": candle_id, "quantity": "2"}],
            )
            try:
                order = await create_custom_order(db, payload)
                return order.id
            except HTTPException as exc:
                assert exc.status_code == 400
                return None

    outcomes = await asyncio.gather(create_for(1), create_for(2))
    async with pg_sessions() as db:
        assert sum(order_id is not None for order_id in outcomes) == 1
        assert await db.scalar(select(func.count(StockMovement.id)).where(
            StockMovement.reference_type == "order",
            StockMovement.movement_type == "ORDER",
        )) == 2
        assert (await db.get(StockItem, flour_id)).stok_tersedia == Decimal("0.0000")
        assert (await db.get(StockItem, candle_id)).stok_tersedia == Decimal("0.0000")


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_expired_lease_is_reclaimed_and_completed(pg_sessions, monkeypatch):
    async with pg_sessions() as db:
        await enqueue_email(db, event_key="pg-expired-lease", recipient="buyer@example.test",
                            event_type="order_status_updated", payload={"status": "ready"})
        event = (await db.execute(select(EmailOutbox))).scalar_one()
        event.status = "sending"
        event.attempts = 1
        event.lease_token = "dead-worker-token"
        event.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    sent_attempts: list[int] = []

    async def fake_send(event):
        sent_attempts.append(event.attempts)

    monkeypatch.setattr("app.services.email_worker._send", fake_send)
    async with pg_sessions() as db:
        from app.services.email_worker import process_one_email
        assert await process_one_email(db) is True

    async with pg_sessions() as db:
        event = (await db.execute(select(EmailOutbox))).scalar_one()
        assert sent_attempts == [2]
        assert event.status == "sent"
        assert event.attempts == 2
        assert event.lease_token is None
        assert event.lease_expires_at is None


async def _create_pending_payment_graph(db, *, pg_order_id):
    customer = Customer(nama="Integration Buyer", nomor_wa="+628123456789")
    db.add(customer)
    db.add(Buyer(name="Integration Buyer", email="buyer@example.test", phone="+628123456789",
                 password_hash="test-hash", is_active=True))
    await db.flush()
    order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                  metode_pengiriman=MetodePengirimanEnum.pickup, total_harga_pesanan=Decimal("100000"))
    db.add(order)
    await db.flush()
    invoice = Invoice(order_id=order.id, nomor_invoice=f"PG-INV-{order.id}",
                      total_tagihan=Decimal("100000"), status=InvoiceStatusEnum.unpaid)
    db.add(invoice)
    await db.flush()
    payment = Payment(invoice_id=invoice.id, pg_transaction_id=f"pg-txn-{order.id}",
                      pg_order_id=pg_order_id, jumlah_bayar=Decimal("100000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    return order.id, invoice.id, payment.id, f"{invoice.nomor_invoice}-PAY-1700000000000-ABCD"


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_payment_transition_commits_payment_invoice_and_outbox(pg_sessions, monkeypatch):
    async def no_chatbot_notification(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.services.payment_service.notify_payment_status", no_chatbot_notification)
    async with pg_sessions() as db:
        _, invoice_id, payment_id, _ = await _create_pending_payment_graph(
            db, pg_order_id="PG-CHARGE-EXACT-1"
        )
        payment = await db.get(Payment, payment_id)
        await _apply_transaction_status(db, payment, {
            "transaction_id": payment.pg_transaction_id,
            "order_id": payment.pg_order_id,
            "gross_amount": "100000.00",
            "transaction_status": "settlement",
        }, commit=True)

    async with pg_sessions() as db:
        payment = await db.get(Payment, payment_id)
        invoice = await db.get(Invoice, invoice_id)
        event = (await db.execute(select(EmailOutbox).where(
            EmailOutbox.event_type == "payment_status_updated"
        ))).scalar_one()
        assert payment.payment_status == PaymentStatusEnum.success
        assert invoice.status == InvoiceStatusEnum.paid
        assert event.event_key.startswith(f"payment_status_updated:{payment_id}:")
        assert event.recipient == "buyer@example.test"
        assert json.loads(event.payload_json)["payment_status"] == PaymentStatusEnum.success.value


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_legacy_pending_payment_requires_exact_order_id_reconciliation(
    pg_sessions, monkeypatch
):
    async with pg_sessions() as db:
        _, invoice_id, payment_id, old_order_reference = await _create_pending_payment_graph(db, pg_order_id=None)
        payment = await db.get(Payment, payment_id)
        payload = {
            "transaction_id": payment.pg_transaction_id,
            "order_id": old_order_reference,
            "status_code": "200",
            "gross_amount": "100000.00",
            "transaction_status": "settlement",
        }
        server_key = "postgres-integration-key"
        monkeypatch.setattr(settings, "midtrans_server_key", server_key)
        payload["signature_key"] = hashlib.sha512(
            f"{payload['order_id']}{payload['status_code']}{payload['gross_amount']}{server_key}".encode()
        ).hexdigest()
        assert (await process_midtrans_webhook(db, payload))["status"] == "error_handled"
        payment = await db.get(Payment, payment_id)
        assert payment is not None

    class FakeResponse:
        status_code = 200

        def json(self):
            return payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    async with pg_sessions() as db:
        payment = await db.get(Payment, payment_id)
        await refresh_if_pending(db, payment)
        await db.refresh(payment)
        invoice = await db.get(Invoice, invoice_id)
        assert payment.payment_status == PaymentStatusEnum.pending
        assert invoice.status == InvoiceStatusEnum.unpaid
        assert await db.scalar(select(func.count(EmailOutbox.id))) == 0

        # Simulate an operator backfill after verifying this exact reference with Midtrans.
        payment.pg_order_id = old_order_reference
        await db.commit()
        await refresh_if_pending(db, payment)
        await db.refresh(payment)
        invoice = await db.get(Invoice, invoice_id)
        assert payment.payment_status == PaymentStatusEnum.success
        assert invoice.status == InvoiceStatusEnum.paid
        assert await db.scalar(select(func.count(EmailOutbox.id))) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_worker_batch_failure_is_retryable_and_visible_in_backlog(
    pg_sessions, monkeypatch, capsys
):
    from app.services import email_worker, email_outbox_cli

    async with pg_sessions() as db:
        await enqueue_email(db, event_key="pg-batch-retry", recipient="buyer@example.test",
                            event_type="order_status_updated", payload={"status": "ready"})
        await db.commit()

    monkeypatch.setattr(email_worker, "AsyncSessionLocal", pg_sessions)

    async def failing_send(_event):
        raise RuntimeError("simulated SMTP outage")

    monkeypatch.setattr(email_worker, "_send", failing_send)
    with pytest.raises(email_worker.EmailBatchDeliveryError):
        await email_worker.run_email_batch(20)

    async with pg_sessions() as db:
        event = (await db.execute(select(EmailOutbox))).scalar_one()
        assert event.status == "pending"
        assert event.attempts == 1
        assert event.last_error == "simulated SMTP outage"
        event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    factory = pg_sessions
    monkeypatch.setattr(email_outbox_cli, "AsyncSessionLocal", factory)
    assert await email_outbox_cli._status() == 0
    assert '"pending": 1' in capsys.readouterr().out

    async def recovered_send(_event):
        return None

    monkeypatch.setattr(email_worker, "_send", recovered_send)
    assert await email_worker.run_email_batch(20) == 1
    async with pg_sessions() as db:
        event = (await db.execute(select(EmailOutbox))).scalar_one()
        assert event.status == "sent"
        assert event.attempts == 2
