import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.mime.multipart import MIMEMultipart

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Buyer, Customer, EmailOutbox, Invoice, Order, Payment, Role, User
from app.models.order import InvoiceStatusEnum, MetodePengirimanEnum, OrderStatusEnum
from app.models.payment import PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem
from app.schemas.order import BuyerOrderCreate, OrderItemCreate
from app.services.email_outbox_service import enqueue_email, enqueue_new_order_for_sellers
from app.services.email_worker import _message_id, _render_email, _send, process_one_email
from app.services.order_service import create_buyer_order, update_order_status
from app.services.payment_service import _apply_transaction_status
from app.services.payment_service import complete_refund_notification


@pytest.fixture
async def email_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _order_graph(db):
    role = Role(nama_role="Staff", level=3)
    db.add(role)
    await db.flush()
    seller = User(username="seller", email="seller@example.test", password_hash="x", role_id=role.id)
    buyer = Buyer(name="Buyer <Test>", email="buyer@example.test", phone="628123456789", password_hash="x")
    customer = Customer(nama="Buyer", nomor_wa="628123456789")
    db.add_all([seller, buyer, customer])
    await db.flush()
    order = Order(customer_id=customer.id, status=OrderStatusEnum.pending,
                  metode_pengiriman=MetodePengirimanEnum.pickup, total_harga_pesanan=Decimal("100000"))
    db.add(order)
    await db.flush()
    invoice = Invoice(order_id=order.id, nomor_invoice=f"INV-{order.id}",
                      total_tagihan=Decimal("100000"), status=InvoiceStatusEnum.unpaid)
    db.add(invoice)
    await db.flush()
    payment = Payment(invoice_id=invoice.id, pg_transaction_id=f"txn-{order.id}",
                      jumlah_bayar=Decimal("100000"), payment_method="qris",
                      payment_status=PaymentStatusEnum.pending, payment_type=PaymentTypeEnum.final)
    db.add(payment)
    await db.flush()
    return order, payment


async def _buyer_order_inputs(db):
    role = Role(nama_role="Staff", level=3)
    db.add(role)
    await db.flush()
    db.add(User(username="order-seller", email="orders@example.test", password_hash="x", role_id=role.id))
    buyer = Buyer(name="Buyer Test", email="buyer-order@example.test", phone="628123456789",
                  password_hash="x", is_active=True)
    db.add(buyer)
    stock = StockItem(nama_item="Flour", satuan=SatuanEnum.gram, kategori=KategoriEnum.bahan_baku,
                      harga_per_satuan=Decimal("1"), stok_tersedia=Decimal("100"), version=0)
    product = Product(nama_produk="Cake", harga_jual=Decimal("50000"), is_active=True, is_available=True)
    db.add_all([stock, product])
    await db.flush()
    db.add(Recipe(product_id=product.id, stock_item_id=stock.id, jumlah_dibutuhkan=Decimal("1")))
    await db.flush()
    data = BuyerOrderCreate(
        metode_pengiriman="pickup",
        fulfillment_date=datetime.now(timezone.utc) + timedelta(days=3),
        items=[OrderItemCreate(product_id=product.id, jumlah=1)],
    )
    return buyer, data


@pytest.mark.asyncio
async def test_outbox_event_rolls_back_with_business_transaction(email_db):
    await enqueue_email(email_db, event_key="rollback", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.rollback()
    count = await email_db.scalar(select(func.count(EmailOutbox.id)))
    assert count == 0


@pytest.mark.asyncio
async def test_buyer_order_and_outbox_commit_together(email_db):
    buyer, data = await _buyer_order_inputs(email_db)
    order = await create_buyer_order(email_db, buyer, data)

    stored_order = await email_db.get(Order, order.id)
    events = (await email_db.execute(
        select(EmailOutbox).where(EmailOutbox.event_type == "order_created")
    )).scalars().all()
    assert stored_order is not None
    assert len(events) == 1
    assert events[0].recipient == "orders@example.test"
    assert json.loads(events[0].payload_json)["order_id"] == order.id


@pytest.mark.asyncio
async def test_buyer_order_commit_failure_rolls_back_order_and_outbox(email_db, monkeypatch):
    buyer, data = await _buyer_order_inputs(email_db)

    async def fail_commit():
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(email_db, "commit", fail_commit)
    with pytest.raises(HTTPException):
        await create_buyer_order(email_db, buyer, data)

    order_count = await email_db.scalar(select(func.count(Order.id)))
    event_count = await email_db.scalar(select(func.count(EmailOutbox.id)))
    assert order_count == 0
    assert event_count == 0


@pytest.mark.asyncio
async def test_duplicate_event_key_is_idempotent(email_db):
    for _ in range(2):
        await enqueue_email(email_db, event_key="same-event", recipient="buyer@example.test",
                            event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    assert await email_db.scalar(select(func.count(EmailOutbox.id))) == 1


@pytest.mark.asyncio
async def test_worker_sends_and_marks_event_sent(email_db, monkeypatch):
    await enqueue_email(email_db, event_key="sent", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    sent = []

    async def fake_send(event):
        sent.append(event.event_key)

    monkeypatch.setattr("app.services.email_worker._send", fake_send)
    assert await process_one_email(email_db)
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert sent == ["sent"]
    assert event.status == "sent"
    assert event.sent_at is not None


def test_message_id_is_valid_and_deterministic():
    import re

    event_key = "payment_status_updated:25:buyer+order@example.test"
    message_id = _message_id(event_key)
    assert re.fullmatch(r"<[0-9a-f]{64}@toti-cakery>", message_id)
    assert _message_id(event_key) == message_id
    assert "buyer+order@example.test" not in message_id


@pytest.mark.asyncio
async def test_smtp_send_uses_configured_sender_recipient_and_tls(email_db, monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.core.config import settings
    from app.models.email_outbox import EmailOutbox
    from unittest.mock import AsyncMock

    event = EmailOutbox(
        event_key="mail-config-test", recipient="recipient@example.test",
        event_type="order_status_updated",
        payload_json=json.dumps({"order_number": "ORDER-5", "customer_name": "Buyer", "status": "ready"}),
    )
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_port", 2525)
    monkeypatch.setattr(settings, "smtp_username", "smtp-user")
    monkeypatch.setattr(settings, "smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "smtp_from_email", "sender@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Toti Test")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    send_mock = AsyncMock()
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=send_mock))

    await _send(event)

    send_mock.assert_awaited_once()
    message = send_mock.await_args.args[0]
    smtp_kwargs = send_mock.await_args.kwargs
    assert isinstance(message, MIMEMultipart)
    assert message["From"] == "Toti Test <sender@example.test>"
    assert message["To"] == "recipient@example.test"
    assert smtp_kwargs == {
        "hostname": "smtp.example.test", "port": 2525, "username": "smtp-user",
        "password": "smtp-secret", "start_tls": True, "timeout": 120,
    }


@pytest.mark.asyncio
async def test_smtp_send_propagates_delivery_failure(email_db, monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.core.config import settings
    from app.models.email_outbox import EmailOutbox
    from unittest.mock import AsyncMock

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "sender@example.test")
    send_mock = AsyncMock(side_effect=RuntimeError("SMTP rejected message"))
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=send_mock))
    event = EmailOutbox(event_key="mail-fail", recipient="buyer@example.test",
                        event_type="order_created", payload_json="{}")

    with pytest.raises(RuntimeError, match="SMTP rejected message"):
        await _send(event)
    send_mock.assert_awaited_once()


@pytest.mark.parametrize("event_type,extra", [
    ("order_created", {"total": "100 & <500"}),
    ("order_status_updated", {"status": "<script>alert(1)</script>"}),
    ("payment_status_updated", {"payment_status": "<b>Success</b>"}),
    ("refund_status_updated", {"refund_status": "<img src=x>", "amount": "1 & 2"}),
])
def test_email_templates_escape_untrusted_buyer_and_order_content(event_type, extra):
    from app.models.email_outbox import EmailOutbox

    payload = {
        "order_id": "<svg/onload=alert(1)>",
        "order_number": "ORDER-<img src=x onerror=alert(1)>",
        "customer_name": "<script>alert('buyer')</script>",
        **extra,
    }
    event = EmailOutbox(event_key="unsafe", recipient="buyer@example.test",
                        event_type=event_type, payload_json=json.dumps(payload))
    _, body = _render_email(event)
    assert "<script>" not in body
    assert "<img src=x" not in body
    assert "<svg" not in body
    assert "&lt;script&gt;" in body
    assert "&lt;img" in body


@pytest.mark.asyncio
async def test_worker_persists_smtp_failure_for_retry(email_db, monkeypatch):
    await enqueue_email(email_db, event_key="retry", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()

    async def failed_send(_event):
        raise RuntimeError("smtp unavailable")

    monkeypatch.setattr("app.services.email_worker._send", failed_send)
    assert await process_one_email(email_db)
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert event.status == "pending"
    assert event.attempts == 1
    assert "smtp unavailable" in event.last_error
    next_attempt_at = event.next_attempt_at
    if next_attempt_at.tzinfo is None:
        next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
    assert next_attempt_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_worker_batch_processes_at_most_limit(email_db, monkeypatch):
    from app.services import email_worker

    for index in range(3):
        await enqueue_email(email_db, event_key=f"batch-{index}", recipient="buyer@example.test",
                            event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    sent = []

    async def fake_send(event):
        sent.append(event.event_key)

    monkeypatch.setattr(email_worker, "_send", fake_send)
    monkeypatch.setattr(email_worker, "AsyncSessionLocal", lambda: email_db)
    assert await email_worker.run_email_batch(2) == 2
    assert len(sent) == 2
    remaining = (await email_db.execute(select(EmailOutbox).where(EmailOutbox.status == "pending"))).scalars().all()
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_concurrent_workers_do_not_send_same_live_lease(email_db, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import email_worker

    await enqueue_email(email_db, event_key="concurrent-lease", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    factory = async_sessionmaker(email_db.bind, class_=AsyncSession, expire_on_commit=False)
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    send_count = 0

    async def held_send(_event):
        nonlocal send_count
        send_count += 1
        send_started.set()
        await release_send.wait()

    monkeypatch.setattr(email_worker, "_send", held_send)
    first = asyncio.create_task(email_worker.process_one_email(email_db))
    await send_started.wait()
    async with factory() as second_db:
        assert await email_worker.process_one_email(second_db) is False
    release_send.set()
    assert await first is True
    assert send_count == 1
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    await email_db.refresh(event)
    assert event.status == "sent"


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed(email_db, monkeypatch):
    from app.services import email_worker

    await enqueue_email(email_db, event_key="expired-lease", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    event.status = "sending"
    event.lease_token = "dead-worker-token"
    event.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await email_db.commit()
    sent = []

    async def fake_send(claimed_event):
        sent.append(claimed_event.event_key)

    monkeypatch.setattr(email_worker, "_send", fake_send)
    assert await email_worker.process_one_email(email_db) is True
    await email_db.refresh(event)
    assert sent == ["expired-lease"]
    assert event.status == "sent"
    assert event.attempts == 1
    assert event.lease_token is None


@pytest.mark.asyncio
async def test_crash_after_smtp_acceptance_can_be_recovered_after_lease_expiry(email_db, monkeypatch):
    from app.services import email_worker

    await enqueue_email(email_db, event_key="smtp-accepted-before-crash", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    sent = []

    async def fake_send(event):
        sent.append(event.event_key)

    finish_success = email_worker._finish_success

    async def simulate_crash_before_sent_commit(*_args):
        raise RuntimeError("simulated process crash before sent persistence")

    monkeypatch.setattr(email_worker, "_send", fake_send)
    monkeypatch.setattr(email_worker, "_finish_success", simulate_crash_before_sent_commit)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        await email_worker.process_one_email(email_db)

    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert event.status == "sending"
    event.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await email_db.commit()
    monkeypatch.setattr(email_worker, "_finish_success", finish_success)
    assert await email_worker.process_one_email(email_db) is True
    await email_db.refresh(event)
    assert sent == ["smtp-accepted-before-crash", "smtp-accepted-before-crash"]
    assert event.status == "sent"
    assert event.attempts == 2


@pytest.mark.asyncio
async def test_expired_lease_at_attempt_limit_moves_to_failed(email_db, monkeypatch):
    from app.core.config import settings
    from app.services import email_worker

    await enqueue_email(email_db, event_key="exhausted-lease", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    event.status = "sending"
    event.attempts = 1
    event.lease_token = "expired-at-limit"
    event.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await email_db.commit()
    monkeypatch.setattr(settings, "email_outbox_max_attempts", 1)

    async def should_not_send(_event):
        pytest.fail("An exhausted lease must not send another attempt")

    monkeypatch.setattr(email_worker, "_send", should_not_send)
    assert await email_worker.process_one_email(email_db) is False
    await email_db.refresh(event)
    assert event.status == "failed"
    assert event.lease_token is None
    assert "outcome is unknown" in event.last_error


@pytest.mark.asyncio
async def test_outbox_cli_retry_requires_failed_event_and_records_audit(email_db, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.email_outbox import EmailOutboxAudit
    from app.services import email_outbox_cli

    await enqueue_email(email_db, event_key="failed-cli", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    event.status = "failed"
    event.attempts = 10
    await email_db.commit()
    factory = async_sessionmaker(email_db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(email_outbox_cli, "AsyncSessionLocal", factory)

    assert await email_outbox_cli._retry(event.id, "SMTP fixed", "operator") == 0
    fresh = await email_db.get(EmailOutbox, event.id)
    await email_db.refresh(fresh)
    assert fresh.status == "pending"
    assert fresh.attempts == 0
    audit = (await email_db.execute(select(EmailOutboxAudit))).scalar_one()
    assert (audit.action, audit.actor, audit.reason) == ("retry", "operator", "SMTP fixed")

    assert await email_outbox_cli._retry(event.id, "repeat", "operator") == 2


@pytest.mark.asyncio
async def test_outbox_cli_status_reports_counts_and_ages(email_db, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import email_outbox_cli

    await enqueue_email(email_db, event_key="pending-cli", recipient="private@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await enqueue_email(email_db, event_key="expired-sending-cli", recipient="private@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    expired = (await email_db.execute(select(EmailOutbox).where(
        EmailOutbox.event_key == "expired-sending-cli"
    ))).scalar_one()
    expired.status = "sending"
    expired.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    await email_db.commit()
    factory = async_sessionmaker(email_db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(email_outbox_cli, "AsyncSessionLocal", factory)
    assert await email_outbox_cli._status() == 0
    output = capsys.readouterr().out
    assert '"pending": 1' in output
    assert '"oldest_pending_age_seconds"' in output
    assert '"expired_sending": 1' in output
    assert '"oldest_expired_lease_age_seconds"' in output
    assert "private@example.test" not in output


def test_outbox_cli_requires_configured_token(monkeypatch):
    from app.core.config import settings
    from app.services import email_outbox_cli

    monkeypatch.setattr(settings, "email_outbox_admin_token", "")
    with pytest.raises(PermissionError, match="not configured"):
        email_outbox_cli._authorize()
    monkeypatch.setattr(settings, "email_outbox_admin_token", "correct-token")
    monkeypatch.setattr(email_outbox_cli.getpass, "getpass", lambda _: "wrong-token")
    with pytest.raises(PermissionError, match="Invalid"):
        email_outbox_cli._authorize()
    monkeypatch.setattr(email_outbox_cli.getpass, "getpass", lambda _: "correct-token")
    assert email_outbox_cli._authorize()


@pytest.mark.asyncio
async def test_smtp_failure_does_not_undo_committed_order_status(email_db, monkeypatch):
    order, _ = await _order_graph(email_db)
    await email_db.commit()
    await update_order_status(email_db, order.id, "in_process")

    async def failed_send(_event):
        raise RuntimeError("smtp unavailable")

    monkeypatch.setattr("app.services.email_worker._send", failed_send)
    assert await process_one_email(email_db)
    fresh_order = await email_db.get(Order, order.id)
    assert fresh_order.status == OrderStatusEnum.in_process


@pytest.mark.asyncio
async def test_batch_smtp_failure_records_retry_and_returns_nonzero_cli_exit(email_db, monkeypatch):
    import sys
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import email_worker

    await enqueue_email(email_db, event_key="batch-failure-exit", recipient="buyer@example.test",
                        event_type="order_status_updated", payload={"status": "ready"})
    await email_db.commit()
    factory = async_sessionmaker(email_db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(email_worker, "AsyncSessionLocal", factory)

    async def failed_send(_event):
        raise RuntimeError("simulated SMTP outage")

    monkeypatch.setattr(email_worker, "_send", failed_send)
    with pytest.raises(email_worker.EmailBatchDeliveryError):
        await email_worker.run_email_batch(20)

    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert event.status == "pending"
    assert event.attempts == 1
    assert event.last_error == "simulated SMTP outage"
    def failed_batch(coro):
        coro.close()
        raise email_worker.EmailBatchDeliveryError("1 email delivery attempt(s) failed; outbox retry was recorded")

    monkeypatch.setattr(sys, "argv", ["email_worker", "--once", "--limit", "20"])
    monkeypatch.setattr(email_worker.asyncio, "run", failed_batch)
    with pytest.raises(SystemExit) as exc:
        email_worker.main()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_order_status_transition_enqueues_once_and_rejected_transition_does_not(email_db):
    order, _ = await _order_graph(email_db)
    await email_db.commit()
    updated = await update_order_status(email_db, order.id, "in_process")
    assert updated.status == OrderStatusEnum.in_process
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert event.event_type == "order_status_updated"
    assert event.recipient == "buyer@example.test"
    with pytest.raises(HTTPException) as exc:
        await update_order_status(email_db, order.id, "in_process")
    assert exc.value.status_code == 409
    assert await email_db.scalar(select(func.count(EmailOutbox.id))) == 1


@pytest.mark.asyncio
async def test_payment_email_only_for_valid_transition_and_commit(email_db):
    _, payment = await _order_graph(email_db)
    await email_db.commit()
    await _apply_transaction_status(
        email_db, payment,
        {"transaction_status": "settlement", "transaction_id": payment.pg_transaction_id},
        commit=True,
    )
    event = (await email_db.execute(select(EmailOutbox))).scalar_one()
    assert event.event_type == "payment_status_updated"
    assert json.loads(event.payload_json)["payment_status"] == PaymentStatusEnum.success.value
    await _apply_transaction_status(
        email_db, payment,
        {"transaction_status": "settlement", "transaction_id": payment.pg_transaction_id},
        commit=True,
    )
    assert await email_db.scalar(select(func.count(EmailOutbox.id))) == 1


@pytest.mark.asyncio
async def test_confirmed_refund_enqueues_once(email_db):
    order, payment = await _order_graph(email_db)
    payment.payment_status = PaymentStatusEnum.success
    invoice = (await email_db.execute(select(Invoice).where(Invoice.order_id == order.id))).scalar_one()
    invoice.status = InvoiceStatusEnum.paid
    await email_db.commit()
    payload = {"refunds": [{
        "refund_key": "refund-event-1", "refund_amount": "100000.00",
        "bank_confirmed_at": "2026-10-14 10:00:00",
    }]}
    await complete_refund_notification(email_db, payment, payload)
    await email_db.commit()
    await complete_refund_notification(email_db, payment, payload)
    await email_db.commit()
    events = (await email_db.execute(
        select(EmailOutbox).where(EmailOutbox.event_type == "refund_status_updated")
    )).scalars().all()
    assert len(events) == 1
    assert events[0].recipient == "buyer@example.test"


@pytest.mark.asyncio
async def test_new_order_notifies_only_active_authorized_sellers(email_db):
    order, _ = await _order_graph(email_db)
    outsider_role = Role(nama_role="Other", level=4)
    email_db.add(outsider_role)
    await email_db.flush()
    seller_role = (await email_db.execute(select(Role).where(Role.level == 3))).scalar_one()
    email_db.add(User(username="outsider", email="other@example.test", password_hash="x",
                      role_id=outsider_role.id, is_active=True))
    email_db.add(User(username="inactive", email="inactive@example.test", password_hash="x",
                      role_id=seller_role.id, is_active=False))
    await enqueue_new_order_for_sellers(email_db, order)
    await email_db.commit()
    recipients = set((await email_db.execute(select(EmailOutbox.recipient))).scalars().all())
    assert recipients == {"seller@example.test"}
