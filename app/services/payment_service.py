import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List
import httpx
import secrets
import time
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy import select, func

from app.core.config import settings
from app.core.state_machine import is_valid_payment_transition, is_payment_terminal
from app.models.order import Order, Invoice, OrderStatusEnum, InvoiceStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum, RefundTransaction, RefundStatusEnum
from app.repositories import order_repo, payment_repo
from app.services.chatbot_notify import (
    notify_chatbot_order_event,
    notify_payment_status,
    notify_refund_status,
)

logger = logging.getLogger(__name__)


async def _validate_midtrans_payment_payload(
    db: AsyncSession, payment: Payment, payload: dict
) -> Invoice:
    """Reconcile gateway status data to the persisted payment and invoice."""
    transaction_id = str(payload.get("transaction_id") or "").strip()
    expected_transaction_id = str(payment.pg_transaction_id or "").strip()
    if not transaction_id or (expected_transaction_id and transaction_id != expected_transaction_id):
        raise ValueError("Midtrans transaction_id mismatch")

    invoice = await db.get(Invoice, payment.invoice_id)
    order_reference = str(payload.get("order_id") or "").strip()
    if not invoice:
        raise ValueError("Midtrans order reference mismatch")
    # A format-shaped order_id does not prove which charge it belongs to.
    # Legacy rows must be reconciled and backfilled from a trusted Midtrans
    # record before callbacks or status refreshes can mutate business state.
    if not payment.pg_order_id or order_reference != payment.pg_order_id:
        raise ValueError("Midtrans order reference mismatch")

    try:
        gross_amount = Decimal(str(payload.get("gross_amount")))
        expected_amount = Decimal(str(payment.jumlah_bayar))
        if not gross_amount.is_finite() or not expected_amount.is_finite():
            raise ValueError("Non-finite amount")
    except Exception as exc:
        raise ValueError("Invalid Midtrans gross_amount") from exc
    if gross_amount != expected_amount:
        raise ValueError("Midtrans gross_amount mismatch")
    # A reserved charge can receive a callback before the original /charge
    # response is persisted. Bind its transaction id only after every signed /
    # authenticated reference and amount has matched the local reservation.
    if not expected_transaction_id:
        payment.pg_transaction_id = transaction_id
    return invoice


def _apply_midtrans_payment_instructions(payment: Payment, payload: dict) -> None:
    """Recover payment instructions when the initial charge response was lost."""
    if payment.payment_method == "bank_transfer":
        va_numbers = payload.get("va_numbers") or []
        if va_numbers and va_numbers[0].get("va_number"):
            payment.va_number = str(va_numbers[0]["va_number"])
    elif payment.payment_method == "qris":
        for action in payload.get("actions") or []:
            if action.get("name") == "generate-qr-code" and action.get("url"):
                payment.qris_url = str(action["url"])
                break

async def create_midtrans_charge(
    db: AsyncSession,
    order_id: int,
    payment_method: str,
    payment_type: str,
    amount: Decimal
) -> dict:
    # Lock the order so concurrent charge requests cannot spend the same outstanding balance.
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    order = order_result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan"
        )
    if order.status in (OrderStatusEnum.cancelled, OrderStatusEnum.refunded, OrderStatusEnum.cancelled_settlement_expired):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order yang dibatalkan tidak dapat menerima pembayaran")
    if order.payment_expires_at and order.invoice and order.invoice.status == InvoiceStatusEnum.unpaid:
        payment_expires_at = order.payment_expires_at
        if payment_expires_at.tzinfo is None:
            payment_expires_at = payment_expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= payment_expires_at:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batas pembayaran pesanan sudah berakhir. Buat pesanan baru untuk melanjutkan.")
    if (order.invoice and order.invoice.status == InvoiceStatusEnum.partial
            and payment_type == "full" and order.settlement_due_date):
        settlement_due_date = order.settlement_due_date
        if settlement_due_date.tzinfo is None:
            settlement_due_date = settlement_due_date.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= settlement_due_date:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Batas pelunasan pesanan sudah berakhir.")
    if not order.invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice tidak ditemukan"
        )

    from decimal import ROUND_HALF_UP
    invoice = order.invoice
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice tidak ditemukan")

    totals = await invoice_payment_totals(db, invoice.id)
    remaining = Decimal(str(invoice.total_tagihan)) - totals["net_paid"]
    if remaining <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice sudah lunas")

    if payment_type == "full":
        valid_amount = remaining
    elif payment_type == "dp":
        valid_amount = Decimal(str(invoice.total_tagihan)) * Decimal("0.5")
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment type. Must be 'full' or 'dp'.")

    valid_amount_rounded = valid_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount_rounded <= 0 or amount_rounded != valid_amount_rounded or amount_rounded > remaining:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payment amount. Amount exceeds outstanding balance or does not match payment type.")
    if amount_rounded != Decimal(int(amount_rounded)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Midtrans charge amount must be a whole rupiah amount.")

    # 2a. Cek apakah sudah ada payment Pending yang aktif untuk order ini
    existing_payments = await payment_repo.get_payments_by_order_id(db, order_id)
    for ep in existing_payments:
        if ep.payment_status == PaymentStatusEnum.pending:
            expected_type = PaymentTypeEnum.dp if payment_type == "dp" else PaymentTypeEnum.final
            if (ep.payment_method != payment_method or ep.payment_type != expected_type
                    or Decimal(str(ep.jumlah_bayar)).quantize(Decimal("0.01")) != amount_rounded):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Pembayaran lain masih aktif untuk pesanan ini. Selesaikan atau tunggu status gateway sebelum memilih nominal atau metode lain.",
                )
            logger.info("[PAYMENT_AUDIT] existing_pending | payment_id=%s | order_id=%s", ep.id, order_id)
            # A previous /charge call may have timed out after Midtrans accepted
            # it. Reconcile against the same gateway order before returning the
            # pending payment; never create a second reservation on retry.
            await refresh_if_pending(db, ep)
            await db.refresh(ep)
            if ep.payment_status == PaymentStatusEnum.pending and not (ep.va_number or ep.qris_url):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Status charge belum dapat dipastikan. Periksa kembali status pembayaran sebelum mencoba lagi.",
                )
            return {"payment_id": ep.id, "pg_transaction_id": ep.pg_transaction_id, "va_number": ep.va_number,
                    "qris_url": ep.qris_url, "status": ep.payment_status, "midtrans_response": None,
                    "pg_order_id": ep.pg_order_id,
                    "message": "Tagihan pembayaran sudah ada dan masih aktif."}

    if payment_type == "dp" and any(
        payment.payment_type == PaymentTypeEnum.dp
        and payment.payment_status in (PaymentStatusEnum.success, PaymentStatusEnum.refunded)
        for payment in existing_payments
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="DP sudah diterima untuk pesanan ini. Gunakan tipe pembayaran full untuk pelunasan.",
        )

    # Persist a local reservation before calling Midtrans. A timeout after the
    # gateway accepted the charge must not make a retry create a second charge.
    order_id_midtrans = f"{invoice.nomor_invoice}-PAY-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"
    model_payment_type = PaymentTypeEnum.dp if payment_type == "dp" else PaymentTypeEnum.final
    payment_obj = Payment(
        invoice_id=invoice.id, pg_order_id=order_id_midtrans,
        jumlah_bayar=amount_rounded, payment_method=payment_method,
        payment_status=PaymentStatusEnum.pending, payment_type=model_payment_type,
    )
    db.add(payment_obj)
    await db.flush()

    # DP settlement logic is persisted with the reservation as well.
    if payment_type == "dp" and order.fulfillment_date:
        from datetime import timezone as tz
        wib_tz = tz(timedelta(hours=7))
        f_dt = order.fulfillment_date
        if f_dt.tzinfo is None:
            f_dt = f_dt.replace(tzinfo=tz.utc)
        f_wib = f_dt.astimezone(wib_tz)
        h_minus_1_wib = f_wib.date() - timedelta(days=1)
        settlement_due_wib = datetime(h_minus_1_wib.year, h_minus_1_wib.month, h_minus_1_wib.day, 18, 0, 0, tzinfo=wib_tz)
        order.settlement_due_date = settlement_due_wib.astimezone(tz.utc)
    elif payment_type == "full" and invoice.status == InvoiceStatusEnum.unpaid:
        order.settlement_due_date = None
    await db.commit()

    # 2b. Buat request payload HTTP POST ke Midtrans API Charge (/charge)
    
    payload = {
        "payment_type": payment_method,
        "transaction_details": {
            "order_id": order_id_midtrans,
            "gross_amount": int(amount_rounded)
        }
    }
    expiry_start = None
    expiry_minutes = None
    wib = timezone(timedelta(hours=7))
    if invoice.status == InvoiceStatusEnum.unpaid and order.created_at:
        # Anchor initial payment expiry to checkout, not charge attempt time.
        expiry_start = order.created_at
        if expiry_start.tzinfo is None:
            expiry_start = expiry_start.replace(tzinfo=timezone.utc)
        deadline = order.payment_expires_at or (
            expiry_start + timedelta(minutes=settings.order_unpaid_timeout_minutes)
        )
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        from math import ceil
        expiry_minutes = ceil((deadline - expiry_start).total_seconds() / 60)
    elif invoice.status == InvoiceStatusEnum.partial and order.settlement_due_date:
        # Final payment after DP expires at the agreed settlement deadline.
        from math import ceil
        expiry_start = datetime.now(timezone.utc)
        due_at = order.settlement_due_date
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        expiry_minutes = ceil((due_at - expiry_start).total_seconds() / 60)
    if expiry_start and expiry_minutes and expiry_minutes > 0:
        payload["custom_expiry"] = {
            "order_time": expiry_start.astimezone(wib).strftime("%Y-%m-%d %H:%M:%S %z"),
            "expiry_duration": expiry_minutes,
            "unit": "minute",
        }
    
    if payment_method == "bank_transfer":
        payload["bank_transfer"] = {"bank": "bca"}
        
    url = f"{settings.midtrans_api_url}/charge"
    
    # Otorisasi menggunakan Basic Auth
    encoded_key = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            try:
                res_json = response.json()
            except Exception:
                res_json = {}
    except Exception as e:
        logger.error(f"Failed to charge via Midtrans: {e}")
        # Keep the pending reservation: gateway acceptance is uncertain.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Status charge belum dapat dipastikan. Periksa status pembayaran sebelum mencoba kembali."
        )
    
    # 2c. Validasi status_code dari body JSON Midtrans
    #     Midtrans Core API sering return HTTP 200 tapi body berisi status_code "406" dll.
    midtrans_status_code = str(res_json.get("status_code", ""))
    if not midtrans_status_code.startswith("2"):
        status_msg = res_json.get("status_message", "Unknown Midtrans error")
        logger.warning(
            "[PAYMENT_AUDIT] midtrans_rejected | midtrans_status=%s | "
            "status_message=%s | order_id=%s",
            midtrans_status_code, status_msg, order_id,
        )
        if response.status_code < 500 and midtrans_status_code and midtrans_status_code[0] in "45":
            payment_obj.payment_status = PaymentStatusEnum.failed
            await db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Gagal membuat tagihan pembayaran: {status_msg}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Status charge belum dapat dipastikan. Periksa status pembayaran sebelum mencoba kembali.")
        
    # 3. Ekstrak nomor VA atau QRIS URL dari respons
    va_number = None
    qris_url = None
    
    if payment_method == "bank_transfer" and "va_numbers" in res_json:
        va_number = res_json["va_numbers"][0].get("va_number")
    elif payment_method == "qris" and "actions" in res_json:
        for action in res_json["actions"]:
            if action.get("name") == "generate-qr-code":
                qris_url = action.get("url")
                break
        if not qris_url and len(res_json["actions"]) > 0:
            qris_url = res_json["actions"][0].get("url")
            
    pg_transaction_id = res_json.get("transaction_id")
    
    payment_obj.pg_transaction_id = pg_transaction_id
    payment_obj.va_number = va_number
    payment_obj.qris_url = qris_url
    await db.commit()

    if payment_obj.payment_status == PaymentStatusEnum.pending and not (va_number or qris_url):
        # Some successful HTTP responses omit the payment instructions. Query
        # the same gateway order once; keep the reservation if the result is
        # still uncertain, but do not tell the client it is actionable.
        await refresh_if_pending(db, payment_obj)
        await db.refresh(payment_obj)
        if payment_obj.payment_status == PaymentStatusEnum.pending and not (payment_obj.va_number or payment_obj.qris_url):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Status charge belum dapat dipastikan. Periksa kembali status pembayaran sebelum mencoba lagi.",
            )
    
    logger.info(
        "[PAYMENT_AUDIT] charge_created | payment_id=%s | transaction_id=%s | "
        "method=%s | type=%s | amount=%s | order_id=%s",
        payment_obj.id, pg_transaction_id, payment_method,
        payment_type, amount, order_id,
    )
    
    return {
        "payment_id": payment_obj.id,
        "pg_transaction_id": pg_transaction_id,
        "pg_order_id": payment_obj.pg_order_id,
        "va_number": va_number,
        "qris_url": qris_url,
        "status": payment_obj.payment_status,
        "midtrans_response": res_json
    }

async def _apply_transaction_status(
    db: AsyncSession,
    payment: Payment,
    payload: dict,
    commit: bool = False,
) -> list[tuple[int, str]]:
    """
    Mapping status Midtrans ke PaymentStatusEnum dan otomasi update Invoice/Order.
    
    Dilindungi oleh State Machine — backward transition ditolak secara silent.
    Jika commit=True, fungsi ini mengeksekusi await db.commit() TERLEBIH DAHULU
    sebelum memicu webhook notification ke Chatbot (mencegah race condition uncommitted read).
    Jika commit=False, mengembalikan list[tuple[order_id, event]] untuk dinotifikasi oleh caller sesudah commit.
    """
    txn_status = payload.get("transaction_status")
    status_map = {
        "settlement": PaymentStatusEnum.success,
        "capture": PaymentStatusEnum.success,
        "pending": PaymentStatusEnum.pending,
        "deny": PaymentStatusEnum.failed,
        "cancel": PaymentStatusEnum.failed,
        "expire": PaymentStatusEnum.failed,
    }
    
    new_status = status_map.get(txn_status, PaymentStatusEnum.pending)
    old_status = payment.payment_status

    # ── STATE MACHINE GUARD ──────────────────────────────────────────────────
    if not is_valid_payment_transition(old_status, new_status):
        logger.warning(
            "[PAYMENT_AUDIT] blocked_transition | payment_id=%s | "
            "old_status=%s | attempted_new_status=%s | "
            "transaction_id=%s | midtrans_status=%s",
            payment.id, old_status.value, new_status.value,
            payload.get("transaction_id"), txn_status,
        )
        return []  # Silently skip invalid/idempotent transition

    # ── APPLY TRANSITION ─────────────────────────────────────────────────────
    payment.payment_status = new_status
    await db.flush()
    
    # Structured audit log
    logger.info(
        "[PAYMENT_AUDIT] status_transition | payment_id=%s | "
        "old_status=%s | new_status=%s | "
        "transaction_id=%s | midtrans_order_id=%s | "
        "gross_amount=%s | midtrans_status=%s",
        payment.id, old_status.value, new_status.value,
        payload.get("transaction_id"), payload.get("order_id"),
        payload.get("gross_amount"), txn_status,
    )
    
    events_to_notify: list[tuple[int, str]] = []

    # ── ATURAN OTOMASI: Jika status payment berubah menjadi 'Success' ────────
    if new_status == PaymentStatusEnum.success:
        if not payment.settled_at:
            payment.settled_at = datetime.now(timezone.utc)
        invoice_res = await db.execute(
            select(Invoice).where(Invoice.id == payment.invoice_id)
        )
        invoice = invoice_res.scalars().first()
        if invoice:
            total_success = (await invoice_payment_totals(db, invoice.id))["net_paid"]
            
            old_invoice_status = invoice.status

            # Update status invoice (order.status tetap pending agar dapat diajukan refund sebelum masuk proses produksi)
            if total_success >= Decimal(str(invoice.total_tagihan)):
                invoice.status = InvoiceStatusEnum.paid
            else:
                invoice.status = InvoiceStatusEnum.partial

            if invoice.status != old_invoice_status:
                logger.info(
                    "[PAYMENT_AUDIT] invoice_status_update | invoice_id=%s | "
                    "old_status=%s | new_status=%s | "
                    "total_success=%s | total_tagihan=%s",
                    invoice.id, old_invoice_status.value if hasattr(old_invoice_status, 'value') else old_invoice_status,
                    invoice.status.value, total_success, invoice.total_tagihan,
                )

            events_to_notify.append((invoice.order_id, "paid"))
            from app.services.email_outbox_service import enqueue_order_payment_event
            await enqueue_order_payment_event(
                db, invoice.order_id, "payment_status_updated", f"{payment.id}:{new_status.value}",
                payment_status=new_status.value,
            )

    elif new_status == PaymentStatusEnum.refunded:
        payment.updated_at = datetime.now(timezone.utc)
        invoice_res = await db.execute(
            select(Invoice).where(Invoice.id == payment.invoice_id)
        )
        invoice = invoice_res.scalars().first()
        if invoice:
            old_invoice_status = invoice.status
            invoice.status = InvoiceStatusEnum.refunded
            
            # Ubah status pesanan induk menjadi 'cancelled'
            from sqlalchemy.orm import selectinload
            from app.models.order import OrderItem
            from app.models.product import Product
            from app.models.recipe import Recipe
            order_res = await db.execute(
                select(Order).where(Order.id == invoice.order_id)
                .options(
                    selectinload(Order.order_items)
                    .selectinload(OrderItem.product)
                    .selectinload(Product.recipes)
                    .selectinload(Recipe.stock_item)
                )
            )
            order = order_res.scalars().first()
            if order and order.status != OrderStatusEnum.cancelled:
                order.status = OrderStatusEnum.cancelled
                
                # Kembalikan stok
                from app.services.order_service import _rollback_order_stock
                await _rollback_order_stock(db, order)
                
                logger.info(
                    "[PAYMENT_AUDIT] order_auto_cancelled_refund | order_id=%s | "
                    "trigger=payment_refund",
                    order.id,
                )
            
            logger.info(
                "[PAYMENT_AUDIT] invoice_status_refunded | invoice_id=%s | "
                "old_status=%s",
                invoice.id, old_invoice_status.value if hasattr(old_invoice_status, 'value') else old_invoice_status,
            )

            events_to_notify.append((invoice.order_id, "refunded"))
            from app.services.email_outbox_service import enqueue_order_payment_event
            await enqueue_order_payment_event(
                db, invoice.order_id, "refund_status_updated", f"payment-{payment.id}",
                refund_status="completed", amount=str((await invoice_payment_totals(db, invoice.id))["refunded"]),
            )

    elif new_status == PaymentStatusEnum.failed:
        invoice_res = await db.execute(select(Invoice).where(Invoice.id == payment.invoice_id))
        invoice = invoice_res.scalars().first()
        if invoice:
            from app.services.email_outbox_service import enqueue_order_payment_event
            await enqueue_order_payment_event(
                db, invoice.order_id, "payment_status_updated", f"{payment.id}:{new_status.value}",
                payment_status=new_status.value,
            )
            successful_res = await db.execute(
                select(func.coalesce(func.sum(Payment.jumlah_bayar), 0)).where(
                    Payment.invoice_id == invoice.id,
                    Payment.payment_status == PaymentStatusEnum.success,
                )
            )
            if Decimal(str(successful_res.scalar() or 0)) <= 0:
                from sqlalchemy.orm import selectinload
                from app.models.order import OrderItem
                from app.models.product import Product
                from app.models.recipe import Recipe
                order_res = await db.execute(
                    select(Order).where(Order.id == invoice.order_id)
                    .options(selectinload(Order.order_items).selectinload(OrderItem.product)
                             .selectinload(Product.recipes).selectinload(Recipe.stock_item))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                order = order_res.scalars().first()
                other_pending = await db.scalar(select(Payment.id).where(
                    Payment.invoice_id == invoice.id,
                    Payment.id != payment.id,
                    Payment.payment_status == PaymentStatusEnum.pending,
                    Payment.pg_order_id.is_not(None),
                ).limit(1))
                if order and order.status == OrderStatusEnum.pending and not other_pending:
                    from app.services.order_service import _rollback_order_stock
                    await _rollback_order_stock(db, order)
                    order.status = OrderStatusEnum.cancelled
                    invoice.status = InvoiceStatusEnum.unpaid
                    from app.services.email_outbox_service import enqueue_order_status_for_buyer
                    await enqueue_order_status_for_buyer(db, order, OrderStatusEnum.cancelled.value)
                    events_to_notify.append((order.id, "cancelled"))

    # Jika commit diminta, commit transaksi database TERLEBIH DAHULU,
    # baru kemudian eksekusi panggilan webhook keluar ke Chatbot.
    if commit:
        await db.commit()
        for o_id, evt in events_to_notify:
            if evt == "paid":
                await notify_payment_status(o_id)
            elif evt == "refunded":
                await notify_refund_status(o_id)
            else:
                await notify_chatbot_order_event(o_id, evt)

    return events_to_notify



async def process_midtrans_webhook(db: AsyncSession, payload: dict) -> dict:
    """
    Proses webhook notification dari Midtrans.
    
    Proteksi:
    1. SHA-512 Signature Verification
    2. Row-Level Lock (SELECT ... FOR UPDATE) pada Payment row
    3. Idempotency Guard — skip jika payment sudah di terminal state
    4. State Machine — validasi transisi status satu arah
    5. Structured Audit Logging
    6. Midtrans Always 200 OK Acknowledgment
    """
    try:
        if not isinstance(payload, dict):
            logger.warning("[PAYMENT_AUDIT] invalid_payload | payload is not a dict")
            return {"status": "ok", "message": "Test notification received, dummy order ignored"}

        order_id = payload.get("order_id")
        transaction_status = payload.get("transaction_status")
        status_code = payload.get("status_code")
        gross_amount = payload.get("gross_amount")
        signature_from_payload = payload.get("signature_key")
        pg_transaction_id = payload.get("transaction_id")

        order_id_str = str(order_id or "").strip()
        status_code_str = str(status_code or "").strip()
        gross_amount_str = str(gross_amount or "").strip()
        signature_str = str(signature_from_payload or "").strip()
        txn_status = str(transaction_status or "").strip()
        pg_txn_id = str(pg_transaction_id or "").strip() if pg_transaction_id else None

        # Reject missing or invalid signatures before any database lookup or mutation.
        if not signature_str:
            logger.warning("[PAYMENT_AUDIT] missing_signature | midtrans_order_id=%s", order_id_str)
            return {"status": "error_handled", "message": "Missing signature"}
        server_key = str(settings.midtrans_server_key or "")
        raw_string = f"{order_id_str}{status_code_str}{gross_amount_str}{server_key}"
        calculated_signature = hashlib.sha512(raw_string.encode("utf-8")).hexdigest()
        if not server_key or not hmac.compare_digest(calculated_signature.lower(), signature_str.lower()):
            logger.warning("[PAYMENT_AUDIT] invalid_signature | midtrans_order_id=%s", order_id_str)
            return {"status": "error_handled", "message": "Invalid signature ignored"}

        # 2. Cari data payment dengan ROW-LEVEL LOCK (FOR UPDATE)
        #    mencegah dua webhook simultan memproses Payment row yang sama
        payment = None
        if pg_txn_id:
            result = await db.execute(
                select(Payment)
                .where(Payment.pg_transaction_id == pg_txn_id)
            )
            payment = result.scalars().first()

        if not payment and order_id_str:
            result = await db.execute(
                select(Payment)
                .where(Payment.pg_order_id == order_id_str)
            )
            payment = result.scalars().first()

        if not payment and order_id_str:
            # Fallback: parsing order_id untuk mendapatkan nomor invoice
            if "-PAY-" in order_id_str:
                inv_num = order_id_str.split("-PAY-")[0]
                result = await db.execute(
                    select(Payment)
                    .join(Invoice, Payment.invoice_id == Invoice.id)
                    .where(Invoice.nomor_invoice == inv_num)
                    .order_by(Payment.id.desc())
                )
                payment = result.scalars().first()
            else:
                result = await db.execute(
                    select(Payment)
                    .join(Invoice, Payment.invoice_id == Invoice.id)
                    .where(Invoice.nomor_invoice == order_id_str)
                    .order_by(Payment.id.desc())
                )
                payment = result.scalars().first()

        if not payment:
            logger.warning(
                "[PAYMENT_AUDIT] payment_not_found | transaction_id=%s | "
                "midtrans_order_id=%s",
                pg_txn_id, order_id_str,
            )
            return {"status": "ok", "message": "Test notification received, dummy order ignored"}

        # Serialize callbacks with cancellation and production transitions in
        # the same order -> payment lock order used by the business flows.
        invoice_for_lock = await db.get(Invoice, payment.invoice_id)
        if invoice_for_lock:
            await db.execute(
                select(Order.id).where(Order.id == invoice_for_lock.order_id).with_for_update()
            )
        locked_result = await db.execute(
            select(Payment).where(Payment.id == payment.id).with_for_update()
            .execution_options(populate_existing=True)
        )
        payment = locked_result.scalars().first()
        if not payment:
            await db.rollback()
            return {"status": "ok", "message": "Payment no longer exists"}

        payment_id = payment.id
        try:
            invoice = await _validate_midtrans_payment_payload(db, payment, payload)
        except ValueError as exc:
            await db.rollback()
            logger.warning("[PAYMENT_AUDIT] payment_payload_mismatch | payment_id=%s | reason=%s", payment_id, exc)
            return {"status": "error_handled", "message": str(exc)}

        if txn_status in ("refund", "partial_refund"):
            await complete_refund_notification(db, payment, payload)
            await db.commit()
            if payment.payment_status == PaymentStatusEnum.refunded:
                await notify_refund_status(invoice.order_id)
            return {"status": "success", "payment_status": payment.payment_status}

        # Midtrans capture is only a successful payment when fraud screening accepts it.
        # Challenge/deny/missing values remain pending for a later authoritative callback.
        if txn_status == "capture" and str(payload.get("fraud_status") or "").lower() != "accept":
            await db.commit()
            logger.info(
                "[PAYMENT_AUDIT] capture_not_accepted | payment_id=%s | fraud_status=%s",
                payment.id, payload.get("fraud_status"),
            )
            return {"status": "skipped", "payment_status": payment.payment_status}

        # 3. IDEMPOTENCY GUARD — skip jika payment sudah di terminal state
        # Pengecualian: webhook 'refund' atau 'partial_refund' boleh diproses dari state 'success'
        is_refund_webhook = txn_status in ["refund", "partial_refund"]
        allow_terminal_transition = is_refund_webhook and payment.payment_status == PaymentStatusEnum.success

        if is_payment_terminal(payment.payment_status) and not allow_terminal_transition:
            logger.info(
                "[PAYMENT_AUDIT] idempotent_skip | payment_id=%s | "
                "current_status=%s | webhook_status=%s | "
                "transaction_id=%s",
                payment.id, payment.payment_status.value,
                txn_status, pg_txn_id,
            )
            await db.commit()  # Release row lock
            return {"status": "skipped", "payment_status": payment.payment_status}

        # 4. Apply status transition (dilindungi State Machine di dalam fungsi)
        #    Commit transaksi DB sebelum memicu webhook Chatbot
        await _apply_transaction_status(db, payment, payload, commit=True)
        return {"status": "success", "payment_status": payment.payment_status}

    except Exception as e:
        await db.rollback()
        logger.error(f"Midtrans notification error: {e}", exc_info=True)
        raise


async def refresh_if_pending(db: AsyncSession, payment: Payment) -> Payment:
    """
    Check payment status via Midtrans API dan apply jika sudah settlement.
    
    Dilindungi oleh:
    - Idempotency check (hanya proses jika masih Pending)
    - State Machine (di dalam _apply_transaction_status)
    """
    if payment.payment_status != PaymentStatusEnum.pending:
        return payment  # Sudah terminal, skip
        
    status_reference = payment.pg_transaction_id or payment.pg_order_id
    if not status_reference:
        logger.warning("[PAYMENT_AUDIT] status_refresh_missing_reference | payment_id=%s", payment.id)
        return payment
    url = f"{settings.midtrans_api_url}/{status_reference}/status"
    encoded_key = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {encoded_key}",
        "Accept": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                midtrans_payload = response.json()
                invoice_for_lock = await db.get(Invoice, payment.invoice_id)
                if invoice_for_lock:
                    await db.execute(
                        select(Order.id).where(Order.id == invoice_for_lock.order_id).with_for_update()
                    )
                locked_result = await db.execute(
                    select(Payment).where(Payment.id == payment.id).with_for_update()
                    .execution_options(populate_existing=True)
                )
                payment = locked_result.scalars().first()
                if not payment or payment.payment_status != PaymentStatusEnum.pending:
                    await db.commit()
                    return payment
                try:
                    await _validate_midtrans_payment_payload(db, payment, midtrans_payload)
                except ValueError as exc:
                    logger.warning("[PAYMENT_AUDIT] status_refresh_mismatch | payment_id=%s | reason=%s",
                                   payment.id, exc)
                    payment_id = payment.id
                    await db.rollback()
                    return await db.get(Payment, payment_id)
                _apply_midtrans_payment_instructions(payment, midtrans_payload)
                # Pending is an idempotent status in the state machine, but a
                # status lookup can still recover VA/QR instructions after a
                # lost /charge response. Persist those fields independently.
                if midtrans_payload.get("transaction_status") == "pending":
                    await db.commit()
                    await db.refresh(payment)
                    return payment
                if (midtrans_payload.get("transaction_status") == "capture"
                        and str(midtrans_payload.get("fraud_status") or "").lower() != "accept"):
                    logger.info("[PAYMENT_AUDIT] refresh_capture_not_accepted | payment_id=%s | fraud_status=%s",
                                payment.id, midtrans_payload.get("fraud_status"))
                    await db.commit()
                    return payment
                # Commit DB terlebih dahulu sebelum memicu webhook Chatbot
                await _apply_transaction_status(db, payment, midtrans_payload, commit=True)
                await db.refresh(payment)
    except Exception as e:
        logger.error(f"Failed to refresh pending payment {payment.id} status: {e}")
        await db.rollback()
        # Ignored and fallback to original payment object
    return payment


async def get_payments_by_order(db: AsyncSession, order_id: int) -> List[Payment]:
    return await payment_repo.get_payments_by_order_id(db, order_id)


async def invoice_payment_totals(db: AsyncSession, invoice_id: int) -> dict[str, Decimal]:
    """Return gross settled payments, completed refunds, and net paid for an invoice."""
    gross_res = await db.execute(
        select(func.coalesce(func.sum(Payment.jumlah_bayar), 0)).where(
            Payment.invoice_id == invoice_id,
            Payment.payment_status.in_([PaymentStatusEnum.success, PaymentStatusEnum.refunded]),
        )
    )
    refund_res = await db.execute(
        select(func.coalesce(func.sum(RefundTransaction.refund_amount), 0)).where(
            RefundTransaction.payment_id.in_(select(Payment.id).where(Payment.invoice_id == invoice_id)),
            RefundTransaction.status == RefundStatusEnum.completed,
        )
    )
    gross = Decimal(str(gross_res.scalar() or 0)).quantize(Decimal("0.01"))
    refunded = Decimal(str(refund_res.scalar() or 0)).quantize(Decimal("0.01"))
    return {"gross_paid": gross, "refunded": refunded, "net_paid": gross - refunded}


async def _refresh_refund_payment_state(db: AsyncSession, payment: Payment) -> None:
    totals = await db.execute(
        select(func.coalesce(func.sum(RefundTransaction.refund_amount), 0)).where(
            RefundTransaction.payment_id == payment.id,
            RefundTransaction.status == RefundStatusEnum.completed,
        )
    )
    completed = Decimal(str(totals.scalar() or 0)).quantize(Decimal("0.01"))
    payment_amount = Decimal(str(payment.jumlah_bayar)).quantize(Decimal("0.01"))
    if completed > payment_amount:
        raise HTTPException(status_code=409, detail="Refund kumulatif melebihi jumlah pembayaran")
    payment.payment_status = PaymentStatusEnum.refunded if completed == payment_amount else PaymentStatusEnum.success

    invoice = await db.get(Invoice, payment.invoice_id, with_for_update=True)
    if not invoice:
        return
    invoice_totals = await invoice_payment_totals(db, invoice.id)
    if invoice_totals["net_paid"] < 0 or invoice_totals["net_paid"] > Decimal(str(invoice.total_tagihan)):
        raise HTTPException(status_code=409, detail="Saldo bersih invoice di luar batas")
    if invoice_totals["gross_paid"] > 0 and invoice_totals["refunded"] == invoice_totals["gross_paid"]:
        invoice.status = InvoiceStatusEnum.refunded
        from app.models.order import OrderItem
        from app.models.product import Product
        from app.models.recipe import Recipe
        order_result = await db.execute(
            select(Order).where(Order.id == invoice.order_id).with_for_update()
            .options(selectinload(Order.order_items).selectinload(OrderItem.product)
                     .selectinload(Product.recipes).selectinload(Recipe.stock_item))
            .execution_options(populate_existing=True)
        )
        order = order_result.scalars().first()
        if order:
            from app.core.state_machine import is_valid_order_transition
            if order.status != OrderStatusEnum.refunded and not is_valid_order_transition(order.status, OrderStatusEnum.refunded):
                raise HTTPException(status_code=409, detail="Transisi order ke refunded tidak valid")
            if order.status not in (OrderStatusEnum.cancelled, OrderStatusEnum.refunded):
                from app.services.order_service import _rollback_order_stock
                await _rollback_order_stock(db, order)
            order.status = OrderStatusEnum.refunded
    elif invoice_totals["net_paid"] >= Decimal(str(invoice.total_tagihan)):
        invoice.status = InvoiceStatusEnum.paid
    elif invoice_totals["net_paid"] > 0:
        invoice.status = InvoiceStatusEnum.partial
    else:
        invoice.status = InvoiceStatusEnum.unpaid


async def process_refund(
    db: AsyncSession,
    order_id: int,
    reason: str,
    amount: Optional[Decimal] = None,
    initiated_by: Optional[int] = None,
    initiated_source: str = "seller",
) -> str:
    """Create pending refund records; only confirmed callbacks/admin confirmation complete them."""
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
        .options(selectinload(Order.invoice).selectinload(Invoice.payments))
        .execution_options(populate_existing=True)
    )
    order = order_result.scalars().first()
    if not order or not order.invoice:
        raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")
    payments = await db.execute(
        select(Payment).where(Payment.invoice_id == order.invoice.id)
        .with_for_update().order_by(Payment.id)
    )
    candidates = [p for p in payments.scalars().all()
                  if p.payment_status in (PaymentStatusEnum.success, PaymentStatusEnum.refunded)]
    remaining_by_payment: dict[int, Decimal] = {}
    for payment in candidates:
        sums = await db.execute(
            select(func.coalesce(func.sum(RefundTransaction.refund_amount), 0)).where(
                RefundTransaction.payment_id == payment.id,
                RefundTransaction.status.in_([RefundStatusEnum.completed, RefundStatusEnum.pending]),
            )
        )
        remaining_by_payment[payment.id] = max(
            Decimal("0.00"), Decimal(str(payment.jumlah_bayar)) - Decimal(str(sums.scalar() or 0))
        )
    refundable = sum(remaining_by_payment.values(), Decimal("0.00")).quantize(Decimal("0.01"))
    requested = refundable if amount is None else Decimal(str(amount)).quantize(Decimal("0.01"))
    if requested <= 0 or requested > refundable:
        raise HTTPException(status_code=400, detail="Nominal refund melebihi saldo refundable")

    allocations: list[tuple[Payment, Decimal, RefundTransaction]] = []
    left = requested
    for payment in candidates:
        part = min(left, remaining_by_payment[payment.id])
        if part <= 0:
            continue
        refund = RefundTransaction(
            payment_id=payment.id, refund_amount=part, status=RefundStatusEnum.pending,
            reason=reason, gateway_ref=f"refund-{payment.id}-{secrets.token_hex(8)}",
            initiated_by=initiated_by, initiated_source=initiated_source,
        )
        db.add(refund)
        allocations.append((payment, part, refund))
        left -= part
        if left == 0:
            break
    await db.flush()

    modes: list[str] = []
    for payment, part, refund in allocations:
        mode = "manual"
        if payment.pg_transaction_id:
            url = f"{settings.midtrans_api_url}/{payment.pg_transaction_id}/refund"
            encoded_key = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
            payload = {"refund_key": refund.gateway_ref, "amount": int(part), "reason": reason}
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload, headers={
                        "Authorization": f"Basic {encoded_key}", "Content-Type": "application/json", "Accept": "application/json"
                    })
                body = response.json()
                if response.status_code in (200, 201) and str(body.get("status_code", "")).startswith("2"):
                    mode = "auto"
                elif response.status_code == 416 or str(body.get("status_code", "")) == "416":
                    # Midtrans is already processing a refund for this transaction.
                    # Keep this reservation pending; a refund notification will
                    # reconcile it against the gateway's refund key and amount.
                    mode = "in_progress"
                    logger.info(
                        "[REFUND_AUDIT] refund_already_in_progress | payment_id=%s | "
                        "refund_id=%s | gateway_ref=%s",
                        payment.id, refund.id, refund.gateway_ref,
                    )
                else:
                    logger.warning("[REFUND_AUDIT] refund_request_rejected | payment_id=%s | http=%s", payment.id, response.status_code)
            except Exception as exc:
                logger.error("[REFUND_AUDIT] gateway_refund_request_error | payment_id=%s | error=%s", payment.id, exc)
        refund.initiated_source = f"{initiated_source}:{mode}"
        modes.append(mode)
        logger.info("[REFUND_AUDIT] refund_pending | refund_id=%s | payment_id=%s | amount=%s | source=%s | mode=%s",
                    refund.id, payment.id, part, initiated_source, mode)

    if modes and all(mode == "auto" for mode in modes):
        return "auto"
    if any(mode == "in_progress" for mode in modes):
        return "in_progress"
    return "manual"


async def complete_refund_notification(db: AsyncSession, payment: Payment, payload: dict) -> None:
    """Reconcile the per-refund Midtrans entries; repeated notifications are idempotent."""
    refund_entries = payload.get("refunds") or []
    if not refund_entries and payload.get("refund_key"):
        refund_entries = [{
            "refund_key": payload.get("refund_key"),
            "refund_amount": payload.get("refund_amount"),
            "bank_confirmed_at": payload.get("bank_confirmed_at"),
            "refund_chargeback_id": payload.get("refund_chargeback_id"),
            "reason": payload.get("reason", "Midtrans refund"),
        }]
    for entry in refund_entries:
        key = str(entry.get("refund_key") or "").strip()
        if not key or entry.get("refund_amount") is None:
            continue
        result = await db.execute(select(RefundTransaction).where(RefundTransaction.gateway_ref == key).with_for_update())
        refund = result.scalars().first()
        if not refund:
            amount = Decimal(str(entry["refund_amount"])).quantize(Decimal("0.01"))
            refund = await db.scalar(
                select(RefundTransaction)
                .where(
                    RefundTransaction.payment_id == payment.id,
                    RefundTransaction.refund_amount == amount,
                    RefundTransaction.status == RefundStatusEnum.pending,
                    RefundTransaction.initiated_source.like("%:in_progress"),
                )
                .order_by(RefundTransaction.id)
                .limit(1)
                .with_for_update()
            )
            if refund:
                # HTTP 416 means another refund for this payment is already
                # processing. Bind its later callback to the pending reservation
                # instead of creating a second pending amount for the same refund.
                refund.gateway_ref = key
            else:
                refund = RefundTransaction(payment_id=payment.id, refund_amount=amount, reason=str(entry.get("reason") or "Midtrans refund")[:500],
                                           gateway_ref=key, initiated_source="midtrans", status=RefundStatusEnum.pending)
                db.add(refund)
                await db.flush()
        if refund.payment_id != payment.id:
            raise HTTPException(status_code=400, detail="Refund callback payment mismatch")
        callback_amount = Decimal(str(entry["refund_amount"])).quantize(Decimal("0.01"))
        if callback_amount != Decimal(str(refund.refund_amount)).quantize(Decimal("0.01")):
            raise HTTPException(status_code=400, detail="Refund callback amount mismatch")
        newly_completed = refund.status != RefundStatusEnum.completed and refund.status != RefundStatusEnum.failed and bool(entry.get("bank_confirmed_at"))
        if refund.status != RefundStatusEnum.failed and entry.get("bank_confirmed_at"):
            refund.status = RefundStatusEnum.completed
            refund.completed_at = datetime.now(timezone.utc)
            if newly_completed:
                from app.services.email_outbox_service import enqueue_order_payment_event
                invoice = await db.get(Invoice, payment.invoice_id)
                if invoice:
                    await enqueue_order_payment_event(
                        db, invoice.order_id, "refund_status_updated", f"refund-{refund.id}",
                        refund_status="completed", amount=str(refund.refund_amount),
                    )
    await db.flush()
    await _refresh_refund_payment_state(db, payment)


async def process_manual_payment(
    db: AsyncSession,
    order_id: int,
    amount: Decimal,
    payment_method: str,
    notes: Optional[str] = None,
    verified_by: Optional[int] = None
) -> dict:
    """
    Eksekusi pembayaran manual (CASH, TRANSFER, dll.):
    1. Validasi keberadaan order & invoice (buat invoice jika belum ada).
    2. Buat record transaksi pembayaran baru (status Success).
    3. Update invoice status -> 'paid' (atau 'partial' jika belum lunas).
    4. Status order tetap 'pending' agar konsisten dengan alur online payment & dapat dibatalkan jika diperlukan.
    5. Commit ke database & kirim notifikasi chatbot.
    """
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    order = order_result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan"
        )
    if order.status in (OrderStatusEnum.cancelled, OrderStatusEnum.refunded, OrderStatusEnum.cancelled_settlement_expired):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order yang dibatalkan tidak dapat menerima pembayaran")

    # Pastikan invoice ada
    invoice = order.invoice
    if not invoice:
        inv_res = await db.execute(select(Invoice).where(Invoice.order_id == order.id))
        invoice = inv_res.scalars().first()

    if not invoice:
        invoice = Invoice(
            order_id=order.id,
            nomor_invoice=f"INV-{int(time.time())}-{secrets.token_hex(2).upper()}",
            total_tagihan=order.total_harga_pesanan,
            status=InvoiceStatusEnum.unpaid
        )
        db.add(invoice)
        await db.flush()

    pending_online = await db.scalar(select(Payment.id).where(
        Payment.invoice_id == invoice.id,
        Payment.payment_status == PaymentStatusEnum.pending,
        Payment.pg_order_id.is_not(None),
    ).limit(1))
    if pending_online:
        raise HTTPException(status_code=409, detail="Pembayaran online masih pending; tunggu status gateway sebelum mencatat pembayaran manual")

    totals = await invoice_payment_totals(db, invoice.id)
    remaining = Decimal(str(invoice.total_tagihan)) - totals["net_paid"]
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= 0 or amount > remaining:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pembayaran melebihi sisa tagihan")

    # Buat record transaksi pembayaran baru di database
    pg_txn_id = f"MANUAL-{order.id}-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"
    payment_obj = Payment(
        invoice_id=invoice.id,
        pg_transaction_id=pg_txn_id,
        jumlah_bayar=amount,
        payment_method=payment_method,
        notes=notes,
        verified_by=verified_by,
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.final,
        settled_at=datetime.now(timezone.utc),
    )
    db.add(payment_obj)
    await db.flush()

    # Hitung total pembayaran sukses pada invoice
    total_paid = (await invoice_payment_totals(db, invoice.id))["net_paid"]
    if total_paid >= Decimal(str(invoice.total_tagihan)):
        invoice.status = InvoiceStatusEnum.paid
    elif total_paid > 0:
        invoice.status = InvoiceStatusEnum.partial
    else:
        invoice.status = InvoiceStatusEnum.unpaid

    from app.services.email_outbox_service import enqueue_order_payment_event
    await enqueue_order_payment_event(
        db, order.id, "payment_status_updated", f"{payment_obj.id}:{PaymentStatusEnum.success.value}",
        payment_status=PaymentStatusEnum.success.value,
    )

    await db.commit()
    await db.refresh(order)
    await db.refresh(payment_obj)

    # Kirim webhook notifikasi chatbot
    try:
        await notify_payment_status(order.id, status="paid")
    except Exception as e:
        logger.warning(f"Gagal mengirim notifikasi chatbot untuk order {order.id}: {e}")

    return {
        "success": True,
        "message": "Pembayaran manual berhasil dicatat",
        "payment_id": payment_obj.id,
        "order_id": order.id,
        "amount": amount,
        "payment_method": payment_method,
        "payment_status": "PAID",
        "order_status": order.status.value if hasattr(order.status, "value") else str(order.status),
    }

async def check_expired_settlements(db: AsyncSession) -> int:
    """
    Background task: cek pesanan PARTIALLY_PAID yang sudah melewati settlement_due_date.
    Jika waktu saat ini > settlement_due_date dan masih ada remaining_amount > 0,
    ubah status order menjadi cancelled_settlement_expired dan bebaskan stok.
    """
    from sqlalchemy.orm import selectinload
    from app.models.order import OrderItem
    from app.models.product import Product
    from app.models.recipe import Recipe
    
    now = datetime.now(timezone.utc)
    
    # Find orders with partial payment that have passed settlement_due_date
    result = await db.execute(
        select(Order.id)
        .where(
            Order.settlement_due_date != None,
            Order.settlement_due_date < now,
            Order.status.in_([OrderStatusEnum.pending, OrderStatusEnum.in_process]),
        )
    )
    order_ids = result.scalars().all()

    # End the candidate-selection transaction before processing orders in isolated sessions.
    await db.rollback()

    session_factory = async_sessionmaker(
        bind=db.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    cancelled_count = 0
    for order_id in order_ids:
        from app.services.order_service import _rollback_order_stock
        try:
            async with session_factory() as order_db:
                async with order_db.begin():
                    order_result = await order_db.execute(
                        select(Order)
                        .where(
                            Order.id == order_id,
                            Order.settlement_due_date != None,
                            Order.settlement_due_date < now,
                            Order.status.in_([OrderStatusEnum.pending, OrderStatusEnum.in_process]),
                        )
                        .options(
                            selectinload(Order.invoice),
                            selectinload(Order.order_items)
                            .selectinload(OrderItem.product)
                            .selectinload(Product.recipes)
                            .selectinload(Recipe.stock_item)
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    order = order_result.scalars().first()
                    if not order or not order.invoice:
                        continue
                    # Only cancel if invoice is partial (DP paid, remaining not paid)
                    if order.invoice.status != InvoiceStatusEnum.partial:
                        continue
                    pending_online = await order_db.scalar(select(Payment.id).where(
                        Payment.invoice_id == order.invoice.id,
                        Payment.payment_status == PaymentStatusEnum.pending,
                        Payment.pg_order_id.is_not(None),
                    ).limit(1))
                    if pending_online:
                        continue

                    await _rollback_order_stock(order_db, order)
                    old_status = order.status.value
                    order.status = OrderStatusEnum.cancelled_settlement_expired
                    from app.services.email_outbox_service import enqueue_order_status_for_buyer
                    await enqueue_order_status_for_buyer(
                        order_db, order, OrderStatusEnum.cancelled_settlement_expired.value
                    )
                    from app.services.notification_service import notify_order_status_updated
                    await notify_order_status_updated(
                        order_db, order, old_status,
                        OrderStatusEnum.cancelled_settlement_expired.value,
                    )
        except Exception as e:
            logger.error(f"Failed to process expired settlement for order {order_id}: {e}", exc_info=True)
            continue

        cancelled_count += 1
        await notify_chatbot_order_event(order_id, "cancelled")
        logger.info(
            "[SETTLEMENT_AUDIT] order_cancelled_expired | order_id=%s | "
            "settlement_due_date=%s",
            order_id, order.settlement_due_date,
        )
    
    return cancelled_count
