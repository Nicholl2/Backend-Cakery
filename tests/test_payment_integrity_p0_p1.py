import hashlib
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.customer import Customer
from app.models.order import Invoice, InvoiceStatusEnum, MetodePengirimanEnum, Order, OrderStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem, StockMovement
from app.models.order import OrderItem
from app.services.payment_service import create_midtrans_charge, process_manual_payment, process_midtrans_webhook


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        customer = Customer(nama="Payment test", nomor_wa="081234567890")
        db.add(customer)
        await db.flush()
        order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                      metode_pengiriman=MetodePengirimanEnum.pickup, total_harga_pesanan=Decimal("1000000"))
        db.add(order)
        await db.flush()
        invoice = Invoice(order_id=order.id, nomor_invoice="INV-INTEGRITY",
                          total_tagihan=Decimal("1000000"), status=InvoiceStatusEnum.partial)
        db.add(invoice)
        await db.flush()
        dp = Payment(invoice_id=invoice.id, pg_transaction_id="dp-txn", jumlah_bayar=Decimal("500000"),
                     payment_method="qris", payment_status=PaymentStatusEnum.success, payment_type=PaymentTypeEnum.dp)
        db.add(dp)
        await db.commit()
        yield db, order, invoice, dp
    await engine.dispose()


@pytest.mark.asyncio
async def test_online_full_charge_cannot_overpay_after_dp(db_session):
    db, order, _, _ = db_session
    with pytest.raises(HTTPException) as exc:
        await create_midtrans_charge(db, order.id, "qris", "full", Decimal("1000000"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_payment_rejected_when_invoice_fully_paid(db_session):
    db, order, invoice, _ = db_session
    invoice.status = InvoiceStatusEnum.paid
    db.add(Payment(invoice_id=invoice.id, pg_transaction_id="final-txn", jumlah_bayar=Decimal("500000"),
                   payment_method="qris", payment_status=PaymentStatusEnum.success, payment_type=PaymentTypeEnum.final))
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await create_midtrans_charge(db, order.id, "qris", "full", Decimal("1"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_manual_payment_cannot_exceed_remaining(db_session):
    db, order, invoice, _ = db_session
    invoice_id = invoice.id
    with pytest.raises(HTTPException) as exc:
        await process_manual_payment(db, order.id, Decimal("500001"), "CASH")
    assert exc.value.status_code == 400
    result = await db.execute(select(Payment).where(Payment.invoice_id == invoice_id))
    assert len(result.scalars().all()) == 1


async def _payload(amount="1000000.00", signature=True):
    key = "integrity-test-key"
    settings.midtrans_server_key = key
    body = {"order_id": "INV-INTEGRITY-PAY-1", "status_code": "200", "gross_amount": amount,
            "transaction_status": "settlement", "transaction_id": "dp-txn"}
    if signature:
        body["signature_key"] = hashlib.sha512(f"{body['order_id']}{body['status_code']}{amount}{key}".encode()).hexdigest()
    return body


@pytest.mark.asyncio
async def test_webhook_missing_signature_is_rejected_without_mutation(db_session):
    db, _, _, dp = db_session
    result = await process_midtrans_webhook(db, await _payload(signature=False))
    assert result["status"] == "error_handled"
    await db.refresh(dp)
    assert dp.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_webhook_invalid_signature_is_rejected_without_mutation(db_session):
    db, _, _, dp = db_session
    payload = await _payload()
    payload["signature_key"] = "bad"
    result = await process_midtrans_webhook(db, payload)
    assert result["status"] == "error_handled"
    await db.refresh(dp)
    assert dp.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_webhook_valid_signature_processes_transaction(db_session):
    db, order, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="new-txn", jumlah_bayar=Decimal("500000"),
                      payment_method="qris", payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    body = await _payload("500000.00")
    body["transaction_id"] = "new-txn"
    body["signature_key"] = hashlib.sha512(
        f"{body['order_id']}{body['status_code']}{body['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()
    result = await process_midtrans_webhook(db, body)
    assert result["status"] == "success"
    await db.refresh(payment)
    assert payment.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_webhook_amount_mismatch_is_rejected_without_mutation(db_session):
    db, _, _, dp = db_session
    result = await process_midtrans_webhook(db, await _payload("1.00"))
    assert result["status"] == "error_handled"
    await db.refresh(dp)
    assert dp.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_failed_initial_payment_cancels_order_and_reverses_stock_once(db_session):
    db, order, invoice, dp = db_session
    await db.delete(dp)
    stock = StockItem(nama_item="Flour", satuan=SatuanEnum.gram, kategori=KategoriEnum.bahan_baku,
                      harga_per_satuan=Decimal("1"), stok_tersedia=Decimal("5"), version=0)
    product = Product(id=999, nama_produk="Cake", harga_jual=Decimal("1000000"))
    db.add_all([stock, product])
    await db.flush()
    db.add(Recipe(product_id=product.id, stock_item_id=stock.id, jumlah_dibutuhkan=Decimal("2")))
    db.add(OrderItem(order_id=order.id, product_id=product.id, jumlah=1, subtotal=Decimal("1000000")))
    pending = Payment(invoice_id=invoice.id, pg_transaction_id="initial-txn", jumlah_bayar=Decimal("1000000"),
                      payment_method="qris", payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(pending)
    await db.commit()
    body = await _payload("1000000.00")
    body.update(transaction_status="expire", transaction_id="initial-txn")
    body["signature_key"] = hashlib.sha512(
        f"{body['order_id']}{body['status_code']}{body['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()
    await process_midtrans_webhook(db, body)
    await db.refresh(order)
    await db.refresh(stock)
    assert order.status == OrderStatusEnum.cancelled
    assert Decimal(str(stock.stok_tersedia)) == Decimal("7")
    body["transaction_status"] = "expire"
    await process_midtrans_webhook(db, body)
    await db.refresh(stock)
    movements = (await db.execute(select(StockMovement).where(StockMovement.reference_id == order.id))).scalars().all()
    assert Decimal(str(stock.stok_tersedia)) == Decimal("7")
    assert len(movements) == 1
