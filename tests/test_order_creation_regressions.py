from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import hashlib
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.customer import Customer
from app.models.buyer import Buyer
from app.models.order import Invoice, Order
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem
from app.schemas.order import BuyerOrderCreate, OrderItemCreate, OrderOut
from app.services import order_service
from app.services.buyer_auth_service import change_buyer_phone
from app.core.security import hash_password


@pytest.fixture
async def order_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all([
            Customer(id=1, nama="Order Buyer", nomor_wa="081299900001"),
            Customer(id=2, nama="Concurrent Buyer", nomor_wa="081299900002"),
            StockItem(id=1, nama_item="Flour", satuan=SatuanEnum.gram,
                      kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
                      stok_tersedia=Decimal("10"), version=0),
            Product(id=1, nama_produk="Cake", harga_jual=Decimal("10000"), hpp_total=Decimal("1000"),
                    is_active=True, is_available=True, minimum_order=1, slug="regression-cake"),
        ])
        await db.flush()
        db.add(Recipe(product_id=1, stock_item_id=1, jumlah_dibutuhkan=Decimal("10")))
        await db.commit()
        yield db, factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def isolate_notifications(monkeypatch):
    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr("app.services.notification_service.notify_order_created", noop)
    monkeypatch.setattr("app.services.email_outbox_service.enqueue_new_order_for_sellers", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)


async def make_order(db, *, customer_id=1, quantity=1, key=None):
    return await order_service.create_new_order(
        db, customer_id=customer_id,
        items=[{"product_id": 1, "jumlah": quantity}],
        metode_pengiriman="pickup", fulfillment_date=None,
        email_seller_on_commit=False, idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_order_success_has_consistent_order_invoice_item_and_response(order_db):
    db, _ = order_db
    order = await make_order(db)
    assert order.total_harga_pesanan == Decimal("10000.00")
    assert order.invoice.total_tagihan == order.total_harga_pesanan
    out = OrderOut.model_validate(order)
    assert out.id == order.id
    assert out.amount_due == Decimal("10000.00")
    assert len(out.order_items) == 1
    stock = await db.get(StockItem, 1)
    assert stock.stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio
async def test_insufficient_stock_returns_business_error_without_partial_order(order_db):
    db, _ = order_db
    with pytest.raises(HTTPException) as error:
        await make_order(db, quantity=2)
    assert error.value.status_code == 400
    assert (await db.scalar(select(func.count(Order.id)))) == 0
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("10.00")


@pytest.mark.asyncio
async def test_failure_during_invoice_creation_rolls_back_order_and_stock(order_db, monkeypatch):
    db, _ = order_db
    async def fail_invoice(*args, **kwargs):
        raise RuntimeError("injected invoice failure")

    monkeypatch.setattr(order_service.order_repo, "create_invoice", fail_invoice)
    with pytest.raises(HTTPException) as error:
        await make_order(db)
    assert error.value.status_code == 500
    assert error.value.detail == "Terjadi kesalahan saat memproses pesanan."
    assert (await db.scalar(select(func.count(Order.id)))) == 0
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("10.00")


@pytest.mark.asyncio
async def test_retry_after_commit_returns_same_order_and_does_not_deduct_stock_twice(order_db):
    db, _ = order_db
    first = await make_order(db, key="client-request-123")
    stock_after_first = (await db.get(StockItem, 1)).stok_tersedia
    retry = await make_order(db, key="client-request-123")
    assert retry.id == first.id
    assert (await db.get(StockItem, 1)).stok_tersedia == stock_after_first
    assert (await db.scalar(select(func.count(Order.id)))) == 1
    assert OrderOut.model_validate(retry).notes is None


@pytest.mark.asyncio
async def test_customer_cancellation_reloads_order_after_waiting_on_lock(order_db):
    db, factory = order_db
    order = await make_order(db)
    order_id = order.id
    async with factory() as concurrent_db:
        current = await concurrent_db.get(Order, order_id)
        current.status = "in_process"
        await concurrent_db.commit()

    # `db` still has the pending Order instance loaded from checkout. The locked
    # cancellation query must replace that stale ORM state before validating.
    with pytest.raises(HTTPException) as error:
        await order_service.cancel_order_by_customer(db, order_id)
    assert error.value.status_code == 409
    await db.refresh(order)
    assert order.status.value == "in_process"
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio
async def test_buyer_phone_change_preserves_order_and_payment_customer_link(order_db):
    db, factory = order_db
    order = await make_order(db)
    customer_id = order.customer_id
    order_id = order.id
    buyer = Buyer(name="Order Buyer", email="buyer-link@example.test", phone="6281299900001",
                  password_hash=hash_password("current-password"), is_verified=True)
    db.add(buyer)
    await db.commit()

    await change_buyer_phone(db, buyer, "current-password", "6281299900009")

    async with factory() as verify_db:
        persisted_buyer = await verify_db.get(Buyer, buyer.id)
        customer = await order_service.get_customer_for_buyer(verify_db, persisted_buyer)
        assert customer is not None
        assert customer.id == customer_id
        assert customer.nomor_wa == "6281299900009"
        assert [buyer_order.id for buyer_order in await order_service.get_buyer_orders(verify_db, persisted_buyer)] == [order_id]
        assert (await order_service.get_buyer_order_by_id(verify_db, persisted_buyer, order_id)).id == order_id


@pytest.mark.asyncio
async def test_customer_cancellation_hides_internal_error_and_rolls_back(order_db, monkeypatch):
    db, _ = order_db
    order = await make_order(db)

    async def fail_stock_rollback(*args, **kwargs):
        raise RuntimeError("secret database connection string")

    monkeypatch.setattr(order_service, "_rollback_order_stock", fail_stock_rollback)
    with pytest.raises(HTTPException) as error:
        await order_service.cancel_order_by_customer(db, order.id)

    assert error.value.status_code == 500
    assert "secret database connection string" not in error.value.detail
    await db.refresh(order)
    assert order.status.value == "pending"
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio
async def test_notification_failure_does_not_rollback_valid_order(order_db, monkeypatch):
    db, _ = order_db

    async def fail_notification(*args, **kwargs):
        raise RuntimeError("injected notification failure")

    monkeypatch.setattr("app.services.notification_service.notify_order_created", fail_notification)
    order = await make_order(db)
    assert await db.get(Order, order.id) is not None
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")
    assert (await db.scalar(select(func.count(Invoice.id)))) == 1


@pytest.mark.asyncio
async def test_email_outbox_failure_does_not_rollback_valid_order(order_db, monkeypatch):
    db, _ = order_db

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("injected email outbox failure")

    monkeypatch.setattr("app.services.email_outbox_service.enqueue_new_order_for_sellers", fail_enqueue)
    order = await order_service.create_new_order(
        db, customer_id=1, items=[{"product_id": 1, "jumlah": 1}],
        metode_pengiriman="pickup", email_seller_on_commit=True,
    )
    assert await db.get(Order, order.id) is not None
    assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")
    assert (await db.scalar(select(func.count(Invoice.id)))) == 1


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_different_payload(order_db):
    db, _ = order_db
    await make_order(db, key="same-key")
    with pytest.raises(HTTPException) as error:
        await make_order(db, quantity=2, key="same-key")
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_plain_notes_cannot_impersonate_an_idempotency_marker(order_db):
    db, _ = order_db
    key = "plain-notes-collision"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    ordinary_note = f"customer text \x1eorder-idempotency-v1:{key_hash}:{'0' * 64}:{'0' * 64}\x1e"
    order = await order_service.create_new_order(
        db, customer_id=1, items=[{"product_id": 1, "jumlah": 1}],
        metode_pengiriman="pickup", notes=ordinary_note,
        email_seller_on_commit=False, idempotency_key=key,
    )
    assert OrderOut.model_validate(order).notes == ordinary_note


@pytest.mark.asyncio
async def test_buyer_idempotency_scope_survives_phone_change(order_db):
    db, _ = order_db
    buyer = Buyer(name="Stable Buyer", email="stable@example.test", phone="081299900001",
                  password_hash="hash", is_active=True)
    db.add(buyer)
    await db.commit()
    data = BuyerOrderCreate(
        metode_pengiriman="pickup",
        fulfillment_date=datetime.now(timezone.utc) + timedelta(days=2),
        items=[OrderItemCreate(product_id=1, jumlah=1)],
    )
    first = await order_service.create_buyer_order(db, buyer, data, idempotency_key="buyer-stable-key")
    first_id = first.id

    buyer.phone = "081299900099"
    await db.commit()
    retry = await order_service.create_buyer_order(db, buyer, data, idempotency_key="buyer-stable-key")

    assert retry.id == first_id
    assert (await db.scalar(select(func.count(Order.id)))) == 1
    assert OrderOut.model_validate(retry).notes is None
