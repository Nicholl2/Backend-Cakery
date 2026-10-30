import hashlib
from decimal import Decimal

import pytest
from fastapi import HTTPException
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.customer import Customer
from app.models.buyer import Buyer
from app.models.email_outbox import EmailOutbox
from app.models.order import Invoice, InvoiceStatusEnum, MetodePengirimanEnum, Order, OrderStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem, StockMovement
from app.models.order import OrderItem
from app.services.payment_service import (
    create_midtrans_charge, process_manual_payment, process_midtrans_webhook, refresh_if_pending,
)
from app.services.order_service import cancel_order_by_customer, update_order_status


async def _async_noop(*args, **kwargs):
    return None


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
async def test_charge_reloads_order_state_before_using_a_preloaded_instance(db_session, monkeypatch):
    db, order, _, _ = db_session
    order_id = order.id
    factory = db.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker
    sessions = async_sessionmaker(factory, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as other:
        latest = await other.get(Order, order_id)
        latest.status = OrderStatusEnum.cancelled
        await other.commit()

    async def unexpected_midtrans_call(*args, **kwargs):
        pytest.fail("Cancelled order must not reach Midtrans")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", unexpected_midtrans_call)
    with pytest.raises(HTTPException) as exc:
        await create_midtrans_charge(db, order_id, "qris", "full", Decimal("500000"))
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


@pytest.mark.asyncio
async def test_active_pending_payment_conflicts_with_different_method_or_type(db_session):
    db, order, invoice, _ = db_session
    pending = Payment(invoice_id=invoice.id, pg_order_id="active-charge",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(pending)
    await db.commit()
    with pytest.raises(HTTPException) as method_error:
        await create_midtrans_charge(db, order.id, "bank_transfer", "full", Decimal("500000"))
    assert method_error.value.status_code == 409
    with pytest.raises(HTTPException) as type_error:
        await create_midtrans_charge(db, order.id, "qris", "dp", Decimal("500000"))
    assert type_error.value.status_code == 409
    assert (await db.execute(select(Payment).where(Payment.pg_order_id.is_not(None)))).scalars().all() == [pending]


@pytest.mark.asyncio
async def test_retry_dp_after_successful_settlement_cannot_create_second_charge(db_session, monkeypatch):
    db, order, invoice, _ = db_session
    payment = (await db.execute(select(Payment).where(Payment.invoice_id == invoice.id))).scalars().one()
    payment.pg_order_id = "INV-INTEGRITY-PAY-SETTLED-DP"
    payment.pg_transaction_id = "settled-dp-transaction"
    payment.payment_status = PaymentStatusEnum.pending
    invoice.status = InvoiceStatusEnum.unpaid
    await db.commit()

    key = "settled-dp-webhook-test-key"
    monkeypatch.setattr(settings, "midtrans_server_key", key)
    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr("app.services.chatbot_notify.notify_payment_status", noop)
    monkeypatch.setattr("app.services.email_outbox_service.enqueue_order_payment_event", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    webhook = {
        "order_id": payment.pg_order_id,
        "status_code": "200",
        "gross_amount": "500000.00",
        "transaction_status": "settlement",
        "transaction_id": payment.pg_transaction_id,
    }
    webhook["signature_key"] = hashlib.sha512(
        f"{webhook['order_id']}{webhook['status_code']}{webhook['gross_amount']}{key}".encode()
    ).hexdigest()
    await process_midtrans_webhook(db, webhook)
    await db.refresh(payment)
    assert payment.payment_status == PaymentStatusEnum.success

    async def unexpected_charge(*args, **kwargs):
        pytest.fail("A settled DP retry must not create another Midtrans charge")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", unexpected_charge)
    with pytest.raises(HTTPException) as error:
        await create_midtrans_charge(db, order.id, "qris", "dp", Decimal("500000"))

    assert error.value.status_code == 409
    payments = (await db.execute(select(Payment).where(Payment.invoice_id == invoice.id))).scalars().all()
    assert payments == [await db.get(Payment, payments[0].id)]
    assert payments[0].payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_order_cannot_enter_production_before_minimum_dp_is_received(db_session):
    db, order, invoice, dp = db_session
    dp.payment_status = PaymentStatusEnum.failed
    await db.commit()
    with pytest.raises(HTTPException) as unpaid_error:
        await update_order_status(db, order.id, "in_process")
    assert unpaid_error.value.status_code == 409
    db.add(Payment(invoice_id=invoice.id, jumlah_bayar=Decimal("499999"),
                   payment_method="CASH", payment_status=PaymentStatusEnum.success,
                   payment_type=PaymentTypeEnum.dp))
    await db.commit()
    with pytest.raises(HTTPException) as underpaid_error:
        await update_order_status(db, order.id, "in_process")
    assert underpaid_error.value.status_code == 409
    db.add(Payment(invoice_id=invoice.id, jumlah_bayar=Decimal("1"),
                   payment_method="CASH", payment_status=PaymentStatusEnum.success,
                   payment_type=PaymentTypeEnum.dp))
    await db.commit()
    updated = await update_order_status(db, order.id, "in_process")
    assert updated.status == OrderStatusEnum.in_process


async def _payload(amount="1000000.00", signature=True):
    key = "integrity-test-key"
    settings.midtrans_server_key = key
    body = {"order_id": "INV-INTEGRITY-PAY-1700000000000-ABCD", "status_code": "200", "gross_amount": amount,
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
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="new-txn",
                      pg_order_id="INV-INTEGRITY-PAY-1700000000000-ABCD", jumlah_bayar=Decimal("500000"),
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
async def test_duplicate_and_out_of_order_webhooks_do_not_reverse_success(db_session):
    db, _, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="ordered-txn",
                      pg_order_id="INV-INTEGRITY-PAY-1700000000000-ABCD", jumlah_bayar=Decimal("500000"),
                      payment_method="qris", payment_status=PaymentStatusEnum.pending,
                      payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    settled = await _payload("500000.00")
    settled["transaction_id"] = "ordered-txn"
    settled["signature_key"] = hashlib.sha512(
        f"{settled['order_id']}{settled['status_code']}{settled['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()
    assert (await process_midtrans_webhook(db, settled))["status"] == "success"
    assert (await process_midtrans_webhook(db, settled))["status"] == "skipped"

    stale_pending = {**settled, "transaction_status": "pending"}
    stale_pending["signature_key"] = hashlib.sha512(
        f"{stale_pending['order_id']}{stale_pending['status_code']}{stale_pending['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()
    assert (await process_midtrans_webhook(db, stale_pending))["status"] == "skipped"
    await db.refresh(payment)
    assert payment.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_capture_only_succeeds_after_midtrans_fraud_accept(db_session):
    db, _, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="capture-txn",
                      pg_order_id="INV-INTEGRITY-PAY-1700000000000-ABCD",
                      jumlah_bayar=Decimal("500000"), payment_method="credit_card",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()

    for fraud_status in (None, "challenge", "deny"):
        body = await _payload("500000.00")
        body.update(transaction_id="capture-txn", transaction_status="capture", fraud_status=fraud_status)
        body["signature_key"] = hashlib.sha512(
            f"{body['order_id']}{body['status_code']}{body['gross_amount']}{settings.midtrans_server_key}".encode()
        ).hexdigest()
        result = await process_midtrans_webhook(db, body)
        assert result["status"] == "skipped"
        await db.refresh(payment)
        assert payment.payment_status == PaymentStatusEnum.pending

    body["fraud_status"] = "accept"
    body["signature_key"] = hashlib.sha512(
        f"{body['order_id']}{body['status_code']}{body['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()
    result = await process_midtrans_webhook(db, body)
    assert result["status"] == "success"
    await db.refresh(payment)
    assert payment.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
async def test_webhook_propagates_transient_processing_failure(db_session, monkeypatch):
    db, _, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="retry-txn",
                      pg_order_id="INV-INTEGRITY-PAY-1700000000000-ABCD",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    body = await _payload("500000.00")
    body["transaction_id"] = "retry-txn"

    async def fail_processing(*_args, **_kwargs):
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr("app.services.payment_service._apply_transaction_status", fail_processing)
    with pytest.raises(RuntimeError, match="temporary database failure"):
        await process_midtrans_webhook(db, body)


@pytest.mark.asyncio
async def test_webhook_endpoint_returns_5xx_for_transient_processing_failure(db_session, monkeypatch):
    from app.core.database import get_db
    from app.main import app

    db, *_ = db_session

    async def fail_processing(*_args, **_kwargs):
        raise RuntimeError("temporary database failure")

    async def override_db():
        yield db

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("app.api.routes.payment.payment_service.process_midtrans_webhook", fail_processing)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/payments/notify", json={})
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_webhook_rejects_other_charge_order_id_and_subcent_amount(db_session):
    db, _, invoice, _ = db_session
    expected_order_id = f"{invoice.nomor_invoice}-PAY-1700000000000-ABCD"
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="strict-order-txn",
                      pg_order_id=expected_order_id, jumlah_bayar=Decimal("500000.00"),
                      payment_method="qris", payment_status=PaymentStatusEnum.pending,
                      payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()

    for transaction_id, order_id, gross_amount in (
        ("wrong-transaction-txn", expected_order_id, "500000.00"),
        (payment.pg_transaction_id, f"{invoice.nomor_invoice}-PAY-1700000000001-ABCD", "500000.00"),
        (payment.pg_transaction_id, expected_order_id, "500000.001"),
    ):
        body = {
            "transaction_id": transaction_id,
            "order_id": order_id,
            "status_code": "200",
            "gross_amount": gross_amount,
            "transaction_status": "settlement",
        }
        body["signature_key"] = hashlib.sha512(
            f"{order_id}200{gross_amount}{settings.midtrans_server_key}".encode()
        ).hexdigest()
        result = await process_midtrans_webhook(db, body)
        assert result["status"] == "error_handled"
        await db.refresh(payment)
        assert payment.payment_status == PaymentStatusEnum.pending
        assert await db.scalar(select(EmailOutbox.id).limit(1)) is None


@pytest.mark.asyncio
async def test_charge_persists_exact_midtrans_order_id_and_amount(db_session, monkeypatch):
    db, order, invoice, _ = db_session
    charge_payloads = []

    class FakeResponse:
        def json(self):
            charge = charge_payloads[-1]["transaction_details"]
            return {"status_code": "201", "transaction_id": "created-charge-txn",
                    "order_id": charge["order_id"],
                    "actions": [{"name": "generate-qr-code", "url": "https://qris.example.test/qr"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, **kwargs):
            charge_payloads.append(json)
            return FakeResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    result = await create_midtrans_charge(db, order.id, "qris", "full", Decimal("500000"))
    payment = await db.get(Payment, result["payment_id"])
    assert payment.pg_order_id == charge_payloads[0]["transaction_details"]["order_id"]
    assert payment.jumlah_bayar == Decimal("500000.00")
    assert charge_payloads[0]["transaction_details"]["gross_amount"] == 500000


@pytest.mark.asyncio
async def test_uncertain_charge_keeps_reservation_and_retry_does_not_charge_again(db_session, monkeypatch):
    db, order, _, _ = db_session
    calls = 0

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.TimeoutException("response lost")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await create_midtrans_charge(db, order.id, "qris", "full", Decimal("500000"))
    assert exc.value.status_code == 502
    pending = (await db.execute(select(Payment).where(Payment.invoice_id == order.invoice.id))).scalars().all()
    assert len(pending) == 2
    reserved_id = next(p.id for p in pending if p.pg_order_id)
    with pytest.raises(HTTPException) as retry_error:
        await create_midtrans_charge(db, order.id, "qris", "full", Decimal("500000"))
    assert retry_error.value.status_code == 502
    assert calls == 1
    assert (await db.get(Payment, reserved_id)).payment_status == PaymentStatusEnum.pending


@pytest.mark.asyncio
async def test_charge_retry_recovers_instructions_from_midtrans_status(db_session, monkeypatch):
    db, order, invoice, _ = db_session
    calls = {"post": 0, "get": 0}

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"transaction_id": "recovered-txn", "order_id": charge_order_id,
                    "gross_amount": "500000.00", "transaction_status": "pending",
                    "va_numbers": [{"bank": "bca", "va_number": "123456789"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, json, **kwargs):
            calls["post"] += 1
            raise httpx.TimeoutException("response lost")
        async def get(self, url, **kwargs):
            calls["get"] += 1
            return FakeResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    with pytest.raises(HTTPException):
        await create_midtrans_charge(db, order.id, "bank_transfer", "full", Decimal("500000"))
    reservation = (await db.execute(select(Payment).where(Payment.pg_order_id.is_not(None)))).scalars().one()
    charge_order_id = reservation.pg_order_id
    recovered = await create_midtrans_charge(db, order.id, "bank_transfer", "full", Decimal("500000"))
    assert recovered["payment_id"] == reservation.id
    assert recovered["va_number"] == "123456789"
    assert calls == {"post": 1, "get": 1}
    await db.refresh(reservation)
    assert reservation.payment_status == PaymentStatusEnum.pending
    assert reservation.va_number == "123456789"


@pytest.mark.asyncio
async def test_manual_payment_is_blocked_while_online_charge_pending(db_session):
    db, order, invoice, _ = db_session
    order_id = order.id
    invoice.status = InvoiceStatusEnum.unpaid
    pending = Payment(invoice_id=invoice.id, pg_order_id="pending-midtrans-order",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(pending)
    await db.commit()
    with pytest.raises(HTTPException) as manual_exc:
        await process_manual_payment(db, order_id, Decimal("100000"), "CASH")
    assert manual_exc.value.status_code == 409
    await db.refresh(order)
    assert order.status == OrderStatusEnum.pending


@pytest.mark.asyncio
async def test_cancel_reconciles_and_cancels_pending_midtrans_transaction(db_session, monkeypatch):
    db, order, invoice, dp = db_session
    invoice.status = InvoiceStatusEnum.unpaid
    dp.payment_status = PaymentStatusEnum.failed
    pending = Payment(
        invoice_id=invoice.id,
        pg_order_id="pending-midtrans-order",
        pg_transaction_id="pending-midtrans-transaction",
        jumlah_bayar=Decimal("1000000"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.pending,
        payment_type=PaymentTypeEnum.final,
    )
    db.add(pending)
    await db.commit()
    monkeypatch.setattr(settings, "midtrans_server_key", "test-server-key")
    monkeypatch.setattr("app.services.payment_service.notify_payment_status", _async_noop)
    monkeypatch.setattr("app.services.payment_service.notify_chatbot_order_event", _async_noop)

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

    pending_payload = {
        "transaction_id": "pending-midtrans-transaction",
        "order_id": "pending-midtrans-order",
        "gross_amount": "1000000.00",
        "transaction_status": "pending",
    }
    cancelled_payload = {**pending_payload, "transaction_status": "cancel"}
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, **kwargs):
            calls.append(("get", url))
            return FakeResponse(200, pending_payload)
        async def post(self, url, **kwargs):
            calls.append(("post", url))
            return FakeResponse(200, cancelled_payload)

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    result = await cancel_order_by_customer(db, order.id)

    await db.refresh(order)
    await db.refresh(pending)
    await db.refresh(invoice)
    assert result["status"] == "success"
    assert order.status == OrderStatusEnum.cancelled
    assert pending.payment_status == PaymentStatusEnum.failed
    assert invoice.status == InvoiceStatusEnum.unpaid
    assert [method for method, _url in calls] == ["get", "post"]
    assert calls[1][1].endswith("/pending-midtrans-order/cancel")


@pytest.mark.asyncio
async def test_cancel_keeps_order_active_when_midtrans_cancel_is_unconfirmed(db_session, monkeypatch):
    db, order, invoice, dp = db_session
    invoice.status = InvoiceStatusEnum.unpaid
    dp.payment_status = PaymentStatusEnum.failed
    pending = Payment(
        invoice_id=invoice.id,
        pg_order_id="pending-midtrans-order",
        pg_transaction_id="pending-midtrans-transaction",
        jumlah_bayar=Decimal("1000000"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.pending,
        payment_type=PaymentTypeEnum.final,
    )
    db.add(pending)
    await db.commit()
    monkeypatch.setattr(settings, "midtrans_server_key", "test-server-key")

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "transaction_id": "pending-midtrans-transaction",
                "order_id": "pending-midtrans-order",
                "gross_amount": "1000000.00",
                "transaction_status": "pending",
            }

    class RejectedCancelResponse:
        status_code = 412

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, **kwargs): return FakeResponse()
        async def post(self, url, **kwargs): return RejectedCancelResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await cancel_order_by_customer(db, order.id)

    await db.refresh(order)
    await db.refresh(pending)
    assert exc.value.status_code == 409
    assert order.status == OrderStatusEnum.pending
    assert pending.payment_status == PaymentStatusEnum.pending


@pytest.mark.asyncio
async def test_cancel_does_not_release_stock_for_legacy_pending_gateway_reference(db_session, monkeypatch):
    db, order, invoice, dp = db_session
    invoice.status = InvoiceStatusEnum.unpaid
    dp.payment_status = PaymentStatusEnum.failed
    pending = Payment(
        invoice_id=invoice.id,
        pg_transaction_id="legacy-midtrans-transaction",
        jumlah_bayar=Decimal("1000000"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.pending,
        payment_type=PaymentTypeEnum.final,
    )
    db.add(pending)
    await db.commit()
    monkeypatch.setattr(settings, "midtrans_server_key", "test-server-key")

    def unexpected_gateway_request(*args, **kwargs):
        pytest.fail("Legacy payment without a verified order reference must not reach Midtrans")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", unexpected_gateway_request)
    with pytest.raises(HTTPException) as exc:
        await cancel_order_by_customer(db, order.id)

    await db.refresh(order)
    await db.refresh(pending)
    assert exc.value.status_code == 409
    assert order.status == OrderStatusEnum.pending
    assert pending.payment_status == PaymentStatusEnum.pending


@pytest.mark.asyncio
async def test_cancel_blocks_if_status_reconciliation_finds_settlement(db_session, monkeypatch):
    db, order, invoice, dp = db_session
    invoice.status = InvoiceStatusEnum.unpaid
    dp.payment_status = PaymentStatusEnum.failed
    pending = Payment(
        invoice_id=invoice.id,
        pg_order_id="pending-midtrans-order",
        pg_transaction_id="pending-midtrans-transaction",
        jumlah_bayar=Decimal("1000000"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.pending,
        payment_type=PaymentTypeEnum.final,
    )
    db.add(pending)
    await db.commit()
    monkeypatch.setattr(settings, "midtrans_server_key", "test-server-key")
    monkeypatch.setattr("app.services.payment_service.notify_payment_status", _async_noop)

    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "transaction_id": "pending-midtrans-transaction",
                "order_id": "pending-midtrans-order",
                "gross_amount": "1000000.00",
                "transaction_status": "settlement",
            }

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, **kwargs): return FakeResponse()
        async def post(self, url, **kwargs): pytest.fail("Settled payment must not be cancelled")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc:
        await cancel_order_by_customer(db, order.id)

    await db.refresh(order)
    await db.refresh(pending)
    await db.refresh(invoice)
    assert exc.value.status_code == 409
    assert order.status == OrderStatusEnum.pending
    assert pending.payment_status == PaymentStatusEnum.success
    assert invoice.status == InvoiceStatusEnum.paid


@pytest.mark.asyncio
async def test_charge_rejects_fractional_rupiah_before_calling_midtrans(db_session, monkeypatch):
    db, order, invoice, _ = db_session
    invoice.total_tagihan = Decimal("1000001.00")
    await db.commit()

    async def unexpected_midtrans_call(*args, **kwargs):
        pytest.fail("Midtrans should not be called for a truncated amount")

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", unexpected_midtrans_call)
    with pytest.raises(HTTPException) as exc:
        await create_midtrans_charge(db, order.id, "qris", "dp", Decimal("500000.50"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_webhook_amount_mismatch_is_rejected_without_mutation(db_session):
    db, _, _, dp = db_session
    result = await process_midtrans_webhook(db, await _payload("1.00"))
    assert result["status"] == "error_handled"
    await db.refresh(dp)
    assert dp.payment_status == PaymentStatusEnum.success


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_changes",
    [
        {"transaction_id": "another-transaction"},
        {"gross_amount": "500001.00"},
        {"gross_amount": "500000.001"},
        {"order_id": "INV-INTEGRITY-PAY-1700000000001-ABCD"},
    ],
    ids=["transaction-id-mismatch", "gross-amount-mismatch", "sub-cent-amount-mismatch", "order-id-mismatch"],
)
async def test_status_refresh_mismatch_does_not_mutate_or_enqueue(db_session, monkeypatch, payload_changes):
    db, order, invoice, _ = db_session
    customer = await db.get(Customer, order.customer_id)
    db.add(Buyer(name="Payment buyer", email="buyer@example.test", phone=customer.nomor_wa,
                 password_hash="hash", is_active=True))
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="refresh-txn",
                      pg_order_id=f"{invoice.nomor_invoice}-PAY-1700000000000-ABCD",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()

    payload = {
        "transaction_id": payment.pg_transaction_id,
        "order_id": payment.pg_order_id,
        "gross_amount": "500000.00",
        "transaction_status": "settlement",
    }
    payload.update(payload_changes)

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
    await refresh_if_pending(db, payment)

    await db.refresh(payment)
    await db.refresh(invoice)
    assert payment.payment_status == PaymentStatusEnum.pending
    assert payment.jumlah_bayar == Decimal("500000.00")
    assert invoice.status == InvoiceStatusEnum.partial
    assert await db.scalar(select(EmailOutbox.id).limit(1)) is None


@pytest.mark.asyncio
async def test_status_refresh_valid_payload_updates_payment_and_enqueues(db_session, monkeypatch):
    db, order, invoice, _ = db_session
    customer = await db.get(Customer, order.customer_id)
    db.add(Buyer(name="Payment buyer", email="buyer@example.test", phone=customer.nomor_wa,
                 password_hash="hash", is_active=True))
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="refresh-valid-txn",
                      pg_order_id=f"{invoice.nomor_invoice}-PAY-1700000000000-ABCD",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    payload = {
        "transaction_id": payment.pg_transaction_id,
        "order_id": payment.pg_order_id,
        "gross_amount": "500000.00",
        "transaction_status": "settlement",
    }

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
    await refresh_if_pending(db, payment)

    await db.refresh(payment)
    assert payment.payment_status == PaymentStatusEnum.success
    event = (await db.execute(select(EmailOutbox))).scalar_one()
    assert event.event_type == "payment_status_updated"


@pytest.mark.asyncio
@pytest.mark.parametrize("fraud_status", [None, "challenge", "deny", "accept"])
async def test_status_refresh_capture_requires_accepted_fraud_status(db_session, monkeypatch, fraud_status):
    db, order, invoice, _ = db_session
    customer = await db.get(Customer, order.customer_id)
    db.add(Buyer(name="Capture buyer", email="capture@example.test", phone=customer.nomor_wa,
                 password_hash="hash", is_active=True))
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="refresh-capture-txn",
                      pg_order_id="INV-INTEGRITY-PAY-CAPTURE", jumlah_bayar=Decimal("500000"),
                      payment_method="credit_card", payment_status=PaymentStatusEnum.pending,
                      payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    payload = {"transaction_id": payment.pg_transaction_id, "order_id": payment.pg_order_id,
               "gross_amount": "500000.00", "transaction_status": "capture"}
    if fraud_status is not None:
        payload["fraud_status"] = fraud_status

    class FakeResponse:
        status_code = 200
        def json(self): return payload
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    await refresh_if_pending(db, payment)
    await db.refresh(payment)
    if fraud_status == "accept":
        assert payment.payment_status == PaymentStatusEnum.success
        assert await db.scalar(select(EmailOutbox.id).limit(1)) is not None
    else:
        assert payment.payment_status == PaymentStatusEnum.pending
        assert await db.scalar(select(EmailOutbox.id).limit(1)) is None


@pytest.mark.asyncio
async def test_status_refresh_recovers_reserved_charge_by_midtrans_order_id(db_session, monkeypatch):
    db, _, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_order_id="INV-INTEGRITY-PAY-RECOVERY",
                      jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    payload = {"transaction_id": "recovered-midtrans-txn", "order_id": payment.pg_order_id,
               "gross_amount": "500000.00", "transaction_status": "settlement",
               "actions": [{"name": "generate-qr-code", "url": "https://midtrans.example/qr"}]}
    requested_urls = []

    class FakeResponse:
        status_code = 200
        def json(self): return payload
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, **kwargs):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr("app.services.payment_service.httpx.AsyncClient", FakeClient)
    await refresh_if_pending(db, payment)
    await db.refresh(payment)
    assert requested_urls[0].endswith("/INV-INTEGRITY-PAY-RECOVERY/status")
    assert payment.pg_transaction_id == "recovered-midtrans-txn"
    assert payment.payment_status == PaymentStatusEnum.success
    assert payment.qris_url == "https://midtrans.example/qr"


@pytest.mark.asyncio
async def test_legacy_payment_without_persisted_order_id_fails_closed(db_session, monkeypatch):
    db, _, invoice, _ = db_session
    payment = Payment(invoice_id=invoice.id, pg_transaction_id="legacy-pending-txn",
                      pg_order_id=None, jumlah_bayar=Decimal("500000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.commit()
    payment_id = payment.id
    payload = {
        "transaction_id": payment.pg_transaction_id,
        "order_id": f"{invoice.nomor_invoice}-PAY-1700000000000-ABCD",
        "status_code": "200", "gross_amount": "500000.00", "transaction_status": "settlement",
    }
    payload["signature_key"] = hashlib.sha512(
        f"{payload['order_id']}{payload['status_code']}{payload['gross_amount']}{settings.midtrans_server_key}".encode()
    ).hexdigest()

    webhook_result = await process_midtrans_webhook(db, payload)
    assert webhook_result["status"] == "error_handled"
    payment = await db.get(Payment, payment_id)

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
    await refresh_if_pending(db, payment)

    await db.refresh(payment)
    await db.refresh(invoice)
    assert payment.payment_status == PaymentStatusEnum.pending
    assert invoice.status == InvoiceStatusEnum.partial
    assert await db.scalar(select(EmailOutbox.id).limit(1)) is None


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
    db.add(StockMovement(stock_item_id=stock.id, movement_type="ORDER", quantity=Decimal("-2"),
                         reference_type="order", reference_id=order.id, reason="fixture consumption"))
    pending = Payment(invoice_id=invoice.id, pg_transaction_id="initial-txn",
                      pg_order_id="INV-INTEGRITY-PAY-1700000000000-ABCD", jumlah_bayar=Decimal("1000000"),
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
    reversals = (await db.execute(select(StockMovement).where(
        StockMovement.reference_id == order.id, StockMovement.movement_type == "REVERSAL"
    ))).scalars().all()
    assert Decimal(str(stock.stok_tersedia)) == Decimal("7")
    assert len(reversals) == 1
