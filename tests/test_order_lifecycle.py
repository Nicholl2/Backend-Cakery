from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Buyer, Customer, EmailOutbox, Invoice, Order, Payment
from app.models.order import InvoiceStatusEnum, MetodePengirimanEnum, OrderStatusEnum
from app.models.payment import PaymentStatusEnum, PaymentTypeEnum
from app.services.order_lifecycle_service import process_order_lifecycle_once


@pytest.fixture
async def lifecycle_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _make_order(db, *, invoice_status=InvoiceStatusEnum.unpaid,
                      order_status=OrderStatusEnum.pending, age_minutes=90,
                      fulfillment_delta=None, created_via="web"):
    now = datetime.now(timezone.utc)
    customer = Customer(nama="Buyer", nomor_wa="628123456789")
    db.add(customer)
    await db.flush()
    order = Order(
        customer_id=customer.id,
        status=order_status,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("100000"),
        created_via=created_via,
        created_at=now - timedelta(minutes=age_minutes),
        payment_expires_at=now - timedelta(minutes=age_minutes - 60),
        fulfillment_date=now + fulfillment_delta if fulfillment_delta is not None else None,
    )
    db.add(order)
    await db.flush()
    invoice = Invoice(order_id=order.id, nomor_invoice=f"LIFE-{order.id}",
                      total_tagihan=Decimal("100000"), status=invoice_status)
    db.add(invoice)
    await db.flush()
    return order, invoice, customer


@pytest.mark.asyncio
async def test_expired_unpaid_checkout_is_cancelled_once_and_stock_reversal_is_idempotent(lifecycle_db, monkeypatch):
    order, _invoice, _customer = await _make_order(lifecycle_db)
    await lifecycle_db.commit()

    events = []
    async def notify(order_id, event):
        events.append((order_id, event))
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", notify)

    result = await process_order_lifecycle_once(lifecycle_db)
    await lifecycle_db.refresh(order)
    assert result["cancelled_unpaid"] == 1
    assert order.status == OrderStatusEnum.cancelled
    assert events == [(order.id, "cancelled")]

    result = await process_order_lifecycle_once(lifecycle_db)
    assert result["cancelled_unpaid"] == 0


@pytest.mark.asyncio
async def test_expired_checkout_with_uncertain_gateway_payment_is_not_cancelled(lifecycle_db, monkeypatch):
    order, invoice, _customer = await _make_order(lifecycle_db)
    lifecycle_db.add(Payment(invoice_id=invoice.id, pg_order_id=f"gateway-{order.id}",
                         jumlah_bayar=Decimal("100000"), payment_method="qris",
                         payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final))
    await lifecycle_db.commit()

    async def keep_pending(_db, payment):
        return payment
    monkeypatch.setattr("app.services.payment_service.refresh_if_pending", keep_pending)
    result = await process_order_lifecycle_once(lifecycle_db)
    await lifecycle_db.refresh(order)
    assert result["cancelled_unpaid"] == 0
    assert order.status == OrderStatusEnum.pending


@pytest.mark.asyncio
async def test_paid_order_past_fulfillment_gets_one_overdue_alert(lifecycle_db, monkeypatch):
    buyer = Buyer(name="Buyer", email="buyer@example.test", phone="628123456789",
                  password_hash="x", is_active=True)
    buyer_email = buyer.email
    lifecycle_db.add(buyer)
    await lifecycle_db.flush()
    order, _invoice, customer = await _make_order(
        lifecycle_db,
        invoice_status=InvoiceStatusEnum.paid,
        order_status=OrderStatusEnum.in_process,
        age_minutes=180,
        fulfillment_delta=timedelta(minutes=-1),
    )
    # Link phone-based legacy customers to the registered buyer for email lookup.
    customer.nomor_wa = buyer.phone
    await lifecycle_db.commit()

    events = []
    async def notify(order_id, event):
        events.append((order_id, event))
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", notify)

    result = await process_order_lifecycle_once(lifecycle_db)
    await lifecycle_db.refresh(order)
    assert result["overdue_alerts"] == 1
    assert order.fulfillment_overdue_notified_at is not None
    assert events == [(order.id, "overdue")]
    outbox = (await lifecycle_db.execute(select(EmailOutbox).where(EmailOutbox.event_type == "order_overdue"))).scalars().all()
    assert buyer_email in {event.recipient for event in outbox}

    result = await process_order_lifecycle_once(lifecycle_db)
    assert result["overdue_alerts"] == 0
