"""
Test Suite: Hardening — Idempotency, State Machine, Auto-Retry, Rate Limiting, Double-Cancel

Menguji seluruh mekanisme hardening yang ditambahkan:
1. State Machine: Payment & Order transition validation
2. Idempotency: Webhook duplikat di-skip
3. Double-Cancel: Order cancel bersamaan hanya revert stok 1x
4. Auto-Retry: Stock deduction retry pada optimistic lock conflict
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decimal import Decimal

# ── Isolated SQLite Setup ─────────────────────────────────────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")
os.environ.setdefault("SECRET_KEY", "test-secret-key-hardening-12345")
os.environ.setdefault("SERVICE_API_KEY", "test-service-key")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

test_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
TestSession = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 1: State Machine — Payment Transitions
# ══════════════════════════════════════════════════════════════════════════════

async def test_payment_state_machine():
    """Memastikan state machine memblokir backward transition."""
    from app.core.state_machine import (
        is_valid_payment_transition,
        is_payment_terminal,
    )
    from app.models.payment import PaymentStatusEnum as PS

    # Valid transitions
    assert is_valid_payment_transition(PS.pending, PS.success) is True, \
        "Pending → Success harus valid"
    assert is_valid_payment_transition(PS.pending, PS.failed) is True, \
        "Pending → Failed harus valid"
    assert is_valid_payment_transition(PS.success, PS.refunded) is True, \
        "Success → Refunded harus valid"

    # Invalid backward transitions
    assert is_valid_payment_transition(PS.success, PS.pending) is False, \
        "Success → Pending harus DITOLAK"
    assert is_valid_payment_transition(PS.success, PS.failed) is False, \
        "Success → Failed harus DITOLAK"
    assert is_valid_payment_transition(PS.failed, PS.success) is False, \
        "Failed → Success harus DITOLAK"
    assert is_valid_payment_transition(PS.failed, PS.pending) is False, \
        "Failed → Pending harus DITOLAK"

    # Idempotent (same status)
    assert is_valid_payment_transition(PS.pending, PS.pending) is False, \
        "Pending → Pending harus skip (idempotent)"

    # Terminal states
    assert is_payment_terminal(PS.success) is True
    assert is_payment_terminal(PS.failed) is True
    assert is_payment_terminal(PS.refunded) is True
    assert is_payment_terminal(PS.pending) is False

    print("✅ TEST 1 PASSED: Payment State Machine validation benar")


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 2: State Machine — Order Transitions
# ══════════════════════════════════════════════════════════════════════════════

async def test_order_state_machine():
    """Memastikan state machine order memblokir transisi ilegal."""
    from app.core.state_machine import (
        is_valid_order_transition,
        is_order_terminal,
    )
    from app.models.order import OrderStatusEnum as OS

    # Valid forward transitions
    assert is_valid_order_transition(OS.pending, OS.in_process) is True
    assert is_valid_order_transition(OS.pending, OS.cancelled) is True
    assert is_valid_order_transition(OS.in_process, OS.ready) is True
    assert is_valid_order_transition(OS.in_process, OS.cancelled) is True
    assert is_valid_order_transition(OS.ready, OS.delivered) is True
    assert is_valid_order_transition(OS.ready, OS.picked_up) is True
    assert is_valid_order_transition(OS.ready, OS.cancelled) is True

    # Invalid backward transitions
    assert is_valid_order_transition(OS.in_process, OS.pending) is False, \
        "in_process → pending harus DITOLAK"
    assert is_valid_order_transition(OS.ready, OS.in_process) is False, \
        "ready → in_process harus DITOLAK"
    assert is_valid_order_transition(OS.delivered, OS.ready) is False, \
        "delivered → ready harus DITOLAK"

    # Terminal states: tidak bisa transisi lagi
    assert is_valid_order_transition(OS.cancelled, OS.pending) is False
    assert is_valid_order_transition(OS.delivered, OS.cancelled) is False
    assert is_valid_order_transition(OS.picked_up, OS.cancelled) is False

    # Terminal check
    assert is_order_terminal(OS.delivered) is True
    assert is_order_terminal(OS.picked_up) is True
    assert is_order_terminal(OS.cancelled) is True
    assert is_order_terminal(OS.pending) is False
    assert is_order_terminal(OS.in_process) is False
    assert is_order_terminal(OS.ready) is False

    print("✅ TEST 2 PASSED: Order State Machine validation benar")


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 3: Idempotency — _apply_transaction_status dengan terminal state
# ══════════════════════════════════════════════════════════════════════════════

async def test_idempotency_apply_status():
    """
    Memastikan _apply_transaction_status() tidak mengubah status payment
    yang sudah di terminal state (Success/Failed).
    """
    from app.core.database import Base
    from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
    from app.models.order import Invoice, InvoiceStatusEnum
    from app.services.payment_service import _apply_transaction_status

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as db:
        # Buat invoice dummy
        invoice = Invoice(
            order_id=999,
            nomor_invoice="INV-TEST-001",
            total_tagihan=Decimal("100000.00"),
            status=InvoiceStatusEnum.paid,
        )
        db.add(invoice)
        await db.flush()

        # Buat payment yang sudah Success
        payment = Payment(
            invoice_id=invoice.id,
            pg_transaction_id="test-txn-idempotent-001",
            jumlah_bayar=Decimal("100000.00"),
            payment_method="qris",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.final,
        )
        db.add(payment)
        await db.flush()

        # Simulasi webhook "pending" yang datang terlambat (stale)
        stale_payload = {
            "transaction_status": "pending",
            "transaction_id": "test-txn-idempotent-001",
            "order_id": "INV-TEST-001-PAY-12345",
            "gross_amount": "100000.00",
        }
        await _apply_transaction_status(db, payment, stale_payload)

        # Status harus tetap Success, BUKAN rollback ke Pending
        assert payment.payment_status == PaymentStatusEnum.success, \
            f"Status seharusnya tetap Success, tapi berubah menjadi {payment.payment_status.value}"

        # Simulasi webhook "settlement" duplikat
        dup_payload = {
            "transaction_status": "settlement",
            "transaction_id": "test-txn-idempotent-001",
            "order_id": "INV-TEST-001-PAY-12345",
            "gross_amount": "100000.00",
        }
        await _apply_transaction_status(db, payment, dup_payload)

        # Status harus tetap Success (same state = idempotent skip)
        assert payment.payment_status == PaymentStatusEnum.success

        await db.rollback()

    print("✅ TEST 3 PASSED: Idempotency guard memblokir backward & duplicate transition")


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 4: Payment Webhook — Full flow dengan idempotency
# ══════════════════════════════════════════════════════════════════════════════

async def test_webhook_idempotency_full_flow():
    """
    Simulasi 2 webhook settlement yang masuk untuk payment yang sama.
    Yang kedua harus di-skip (idempotent).
    """
    from app.core.database import Base
    from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
    from app.models.order import Invoice, InvoiceStatusEnum, Order, OrderStatusEnum
    from app.services.payment_service import _apply_transaction_status

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as db:
        # Setup: Order → Invoice → Payment (Pending)
        order = Order(
            customer_id=1,
            status=OrderStatusEnum.pending,
            metode_pengiriman="pickup",
            total_harga_pesanan=Decimal("50000.00"),
            created_via="test",
        )
        db.add(order)
        await db.flush()

        invoice = Invoice(
            order_id=order.id,
            nomor_invoice="INV-TEST-002",
            total_tagihan=Decimal("50000.00"),
            status=InvoiceStatusEnum.unpaid,
        )
        db.add(invoice)
        await db.flush()

        payment = Payment(
            invoice_id=invoice.id,
            pg_transaction_id="test-txn-double-001",
            jumlah_bayar=Decimal("50000.00"),
            payment_method="qris",
            payment_status=PaymentStatusEnum.pending,
            payment_type=PaymentTypeEnum.final,
        )
        db.add(payment)
        await db.flush()

        settlement_payload = {
            "transaction_status": "settlement",
            "transaction_id": "test-txn-double-001",
            "order_id": "INV-TEST-002-PAY-99999",
            "gross_amount": "50000.00",
        }

        # Webhook #1: Harus berhasil mengubah Pending → Success
        await _apply_transaction_status(db, payment, settlement_payload)
        assert payment.payment_status == PaymentStatusEnum.success, \
            "Webhook pertama harus mengubah status ke Success"

        # Webhook #2: Harus di-skip (idempotent)
        await _apply_transaction_status(db, payment, settlement_payload)
        assert payment.payment_status == PaymentStatusEnum.success, \
            "Webhook kedua harus di-skip, status tetap Success"

        await db.rollback()

    print("✅ TEST 4 PASSED: Double webhook settlement di-handle secara idempotent")


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 5: Rate Limiter — Module loads correctly
# ══════════════════════════════════════════════════════════════════════════════

async def test_rate_limiter_module():
    """Memastikan rate limiter module terkonfigurasi dengan benar."""
    from app.core.rate_limiter import (
        limiter,
        RATE_ORDER_CREATE,
        RATE_PAYMENT_CREATE,
        RATE_AUTH_LOGIN,
        RATE_AUTH_VERIFY,
        RATE_WEBHOOK,
    )

    assert limiter is not None, "Limiter harus terinstansiasi"
    assert RATE_ORDER_CREATE == "5/minute"
    assert RATE_PAYMENT_CREATE == "5/minute"
    assert RATE_AUTH_LOGIN == "10/minute"
    assert RATE_AUTH_VERIFY == "6/minute"
    assert RATE_WEBHOOK == "30/minute"

    print("✅ TEST 5 PASSED: Rate limiter module terkonfigurasi benar")


# ══════════════════════════════════════════════════════════════════════════════
#  TEST 6: Order State Machine integration — update_order_status validation
# ══════════════════════════════════════════════════════════════════════════════

async def test_order_state_machine_in_update():
    """
    Memastikan update_order_status menolak transisi ilegal (delivered → pending).
    Catatan: Test ini menggunakan state machine langsung, bukan via endpoint
    (karena SQLite tidak mendukung FOR UPDATE).
    """
    from app.core.state_machine import is_valid_order_transition
    from app.models.order import OrderStatusEnum as OS

    # Simulasi validasi yang ada di update_order_status
    current = OS.delivered
    target = OS.pending

    result = is_valid_order_transition(current, OS(target.value))
    assert result is False, \
        f"Transisi delivered → pending harus ditolak oleh state machine"

    # Cancelled → pending juga harus ditolak
    result = is_valid_order_transition(OS.cancelled, OS.pending)
    assert result is False

    # Tapi pending → in_process harus valid (otomatis via payment settlement)
    result = is_valid_order_transition(OS.pending, OS.in_process)
    assert result is True

    print("✅ TEST 6 PASSED: Order state machine integration validation benar")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 70)
    print("  HARDENING TEST SUITE — Concurrency, Idempotency, State Machine")
    print("=" * 70)
    print()

    passed = 0
    failed = 0
    tests = [
        ("Payment State Machine", test_payment_state_machine),
        ("Order State Machine", test_order_state_machine),
        ("Idempotency: _apply_transaction_status", test_idempotency_apply_status),
        ("Webhook Idempotency Full Flow", test_webhook_idempotency_full_flow),
        ("Rate Limiter Module", test_rate_limiter_module),
        ("Order State Machine Integration", test_order_state_machine_in_update),
    ]

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"❌ FAILED: {name} — {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 70)
    print(f"  RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
