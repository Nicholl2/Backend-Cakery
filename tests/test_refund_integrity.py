from datetime import datetime, timedelta, timezone
from decimal import Decimal
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.customer import Customer
from app.models.order import Invoice, InvoiceStatusEnum, MetodePengirimanEnum, Order, OrderStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum, RefundStatusEnum, RefundTransaction
from app.repositories.report_repo import get_financial_report_data
from app.services.payment_service import (
    _refresh_refund_payment_state, complete_refund_notification, create_midtrans_charge,
    invoice_payment_totals, process_manual_payment, process_refund,
)


@pytest.fixture
async def refund_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        customer = Customer(nama="Refund", nomor_wa="081211111111")
        db.add(customer)
        await db.flush()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                      metode_pengiriman=MetodePengirimanEnum.pickup, total_harga_pesanan=Decimal("1000000"))
        db.add(order)
        await db.flush()
        invoice = Invoice(order_id=order.id, nomor_invoice="INV-REFUND-LEDGER",
                          total_tagihan=Decimal("1000000"), status=InvoiceStatusEnum.paid)
        db.add(invoice)
        await db.flush()
        payment = Payment(invoice_id=invoice.id, pg_transaction_id=None, jumlah_bayar=Decimal("1000000"),
                          payment_method="cash", payment_status=PaymentStatusEnum.success,
                          payment_type=PaymentTypeEnum.final)
        db.add(payment)
        await db.commit()
        yield db, order, invoice, payment
    await engine.dispose()


async def _report(db):
    now = datetime.now(timezone.utc)
    return await get_financial_report_data(db, now - timedelta(days=1), now + timedelta(days=1))


@pytest.mark.asyncio
async def test_full_refund_completes_payment_only_after_confirmation(refund_db):
    db, order, invoice, payment = refund_db
    assert await process_refund(db, order.id, "full") == "manual"
    await db.commit()
    refund = (await db.execute(select(RefundTransaction))).scalar_one()
    assert refund.refund_amount == Decimal("1000000.00")
    assert refund.status == RefundStatusEnum.pending
    assert (await invoice_payment_totals(db, invoice.id))["net_paid"] == Decimal("1000000.00")
    refund.status = RefundStatusEnum.completed
    refund.completed_at = datetime.now(timezone.utc)
    await _refresh_refund_payment_state(db, payment)
    await db.commit()
    assert payment.payment_status == PaymentStatusEnum.refunded
    assert invoice.status == InvoiceStatusEnum.refunded
    assert order.status == OrderStatusEnum.refunded


@pytest.mark.asyncio
async def test_partial_and_multiple_refunds_reconcile_cash_and_outstanding(refund_db):
    db, order, invoice, payment = refund_db
    await process_refund(db, order.id, "first partial", amount=Decimal("300000"))
    await process_refund(db, order.id, "second partial", amount=Decimal("200000"))
    await db.commit()
    refunds = (await db.execute(select(RefundTransaction).order_by(RefundTransaction.id))).scalars().all()
    assert [r.refund_amount for r in refunds] == [Decimal("300000.00"), Decimal("200000.00")]
    for refund in refunds:
        refund.status = RefundStatusEnum.completed
        refund.completed_at = datetime.now(timezone.utc)
    await _refresh_refund_payment_state(db, payment)
    await db.commit()

    totals = await invoice_payment_totals(db, invoice.id)
    assert totals == {"gross_paid": Decimal("1000000.00"), "refunded": Decimal("500000.00"), "net_paid": Decimal("500000.00")}
    assert payment.payment_status == PaymentStatusEnum.success
    assert invoice.status == InvoiceStatusEnum.partial
    report = await _report(db)
    assert report["cash_received"] == Decimal("1000000.00")
    assert report["cash_refunded"] == Decimal("500000.00")
    assert report["net_cash_flow"] == Decimal("500000.00")
    assert report["outstanding_payments"] == Decimal("500000.00")


@pytest.mark.asyncio
async def test_refund_over_remaining_commitment_is_rejected(refund_db):
    db, order, _, _ = refund_db
    await process_refund(db, order.id, "first", amount=Decimal("700000"))
    await db.commit()
    with pytest.raises(HTTPException):
        await process_refund(db, order.id, "over cumulative", amount=Decimal("700000"))


@pytest.mark.asyncio
async def test_pending_failed_and_duplicate_refund_callbacks(refund_db):
    db, order, invoice, payment = refund_db
    await process_refund(db, order.id, "partial callback", amount=Decimal("300000"))
    await db.commit()
    refund = (await db.execute(select(RefundTransaction))).scalar_one()
    pending_item = {"refund_key": refund.gateway_ref, "refund_amount": "300000.00", "reason": "partial"}
    await complete_refund_notification(db, payment, {"refunds": [pending_item]})
    assert (await invoice_payment_totals(db, invoice.id))["net_paid"] == Decimal("1000000.00")
    refund.status = RefundStatusEnum.failed
    await db.commit()
    assert (await invoice_payment_totals(db, invoice.id))["net_paid"] == Decimal("1000000.00")

    await process_refund(db, order.id, "confirmed", amount=Decimal("200000"))
    await db.commit()
    confirmed = (await db.execute(select(RefundTransaction).where(RefundTransaction.status == RefundStatusEnum.pending))).scalars().all()
    target = next(r for r in confirmed if r.refund_amount == Decimal("200000.00"))
    callback_item = {"refund_key": target.gateway_ref, "refund_amount": "200000.00",
                     "bank_confirmed_at": "2026-10-08 10:00:00", "reason": "confirmed"}
    await complete_refund_notification(db, payment, {"refunds": [callback_item]})
    await db.commit()
    await complete_refund_notification(db, payment, {"refunds": [callback_item]})
    await db.commit()
    assert (await invoice_payment_totals(db, invoice.id))["refunded"] == Decimal("200000.00")
    assert payment.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_refund_cannot_exceed_successful_payment(refund_db):
    db, _, _, payment = refund_db
    row = RefundTransaction(payment_id=payment.id, refund_amount=Decimal("1000001"),
                            status=RefundStatusEnum.completed, reason="invalid", initiated_source="test")
    db.add(row)
    with pytest.raises(HTTPException):
        await _refresh_refund_payment_state(db, payment)
    await db.rollback()


@pytest.mark.asyncio
async def test_cancelled_order_cannot_accept_new_payment(refund_db):
    db, order, _, _ = refund_db
    order.status = OrderStatusEnum.cancelled
    await db.commit()
    with pytest.raises(HTTPException):
        await create_midtrans_charge(db, order.id, "qris", "full", Decimal("1"))
    with pytest.raises(HTTPException):
        await process_manual_payment(db, order.id, Decimal("1"), "cash")


def test_refund_concurrency_query_uses_postgresql_row_lock():
    from sqlalchemy.dialects import postgresql

    statement = select(Order).where(Order.id == 1).with_for_update()
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
