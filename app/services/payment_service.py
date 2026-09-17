import base64
import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
import httpx
import secrets
import time
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import settings
from app.core.state_machine import is_valid_payment_transition, is_payment_terminal
from app.models.order import Order, Invoice, OrderStatusEnum, InvoiceStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.repositories import order_repo, payment_repo
from app.services.chatbot_notify import (
    notify_chatbot_order_event,
    notify_payment_status,
    notify_refund_status,
)

logger = logging.getLogger(__name__)

async def create_midtrans_charge(
    db: AsyncSession,
    order_id: int,
    payment_method: str,
    payment_type: str,
    amount: Decimal
) -> dict:
    # 1. Ambil data order dan invoice terkait
    order = await order_repo.get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan"
        )
    if not order.invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice tidak ditemukan"
        )

    # Validasi Nominal Payment (Anti-Tampering)
    from decimal import ROUND_HALF_UP
    if payment_type == "full":
        valid_amount = order.total_harga_pesanan
    elif payment_type == "dp":
        valid_amount = order.total_harga_pesanan * Decimal("0.5")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payment type. Must be 'full' or 'dp'."
        )

    valid_amount_rounded = valid_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if amount_rounded != valid_amount_rounded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payment amount. Amount does not match order calculation."
        )
        
    invoice = order.invoice
    
    # 2a. Cek apakah sudah ada payment Pending yang aktif untuk order ini
    existing_payments = await payment_repo.get_payments_by_order_id(db, order_id)
    for ep in existing_payments:
        if ep.payment_status == PaymentStatusEnum.pending:
            logger.info(
                "[PAYMENT_AUDIT] existing_pending | payment_id=%s | order_id=%s",
                ep.id, order_id,
            )
            return {
                "payment_id": ep.id,
                "pg_transaction_id": ep.pg_transaction_id,
                "va_number": ep.va_number,
                "qris_url": ep.qris_url,
                "status": ep.payment_status,
                "midtrans_response": None,
                "message": "Tagihan pembayaran sudah ada dan masih aktif.",
            }
    
    # 2b. Buat request payload HTTP POST ke Midtrans API Charge (/charge)
    # Order ID di Midtrans dikombinasikan dengan suffix agar unik
    order_id_midtrans = f"{invoice.nomor_invoice}-PAY-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"
    
    payload = {
        "payment_type": payment_method,
        "transaction_details": {
            "order_id": order_id_midtrans,
            "gross_amount": int(amount)
        }
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
            res_json = response.json()
    except Exception as e:
        logger.error(f"Failed to charge via Midtrans: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gagal memproses pembayaran ke Midtrans: {str(e)}"
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Gagal membuat tagihan pembayaran: {status_msg}"
        )
        
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
    
    # Tentukan tipe pembayaran (DP/Final) berdasarkan perbandingan tagihan
    model_payment_type = PaymentTypeEnum.dp if payment_type == "dp" else PaymentTypeEnum.final
    
    # 4. Simpan record baru di tabel payments dengan status "Pending"
    payment_obj = Payment(
        invoice_id=invoice.id,
        pg_transaction_id=pg_transaction_id,
        jumlah_bayar=amount,
        payment_method=payment_method,
        payment_status=PaymentStatusEnum.pending,
        payment_type=model_payment_type,
        va_number=va_number,
        qris_url=qris_url
    )
    
    await payment_repo.create_payment(db, payment_obj)
    await db.commit()
    
    logger.info(
        "[PAYMENT_AUDIT] charge_created | payment_id=%s | transaction_id=%s | "
        "method=%s | type=%s | amount=%s | order_id=%s",
        payment_obj.id, pg_transaction_id, payment_method,
        payment_type, amount, order_id,
    )
    
    return {
        "payment_id": payment_obj.id,
        "pg_transaction_id": pg_transaction_id,
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
        "refund": PaymentStatusEnum.refunded,
        "partial_refund": PaymentStatusEnum.refunded
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
            # Hitung total pembayaran sukses untuk invoice ini
            sum_res = await db.execute(
                select(func.sum(Payment.jumlah_bayar))
                .where(
                    Payment.invoice_id == invoice.id,
                    Payment.payment_status == PaymentStatusEnum.success
                )
            )
            total_success = sum_res.scalar() or Decimal("0.00")
            total_success = Decimal(str(total_success))
            
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

        # 1. Validasi integritas request menggunakan SHA512 Signature Key jika signature disertakan
        if signature_str and settings.midtrans_server_key:
            server_key = str(settings.midtrans_server_key or "")
            raw_string = f"{order_id_str}{status_code_str}{gross_amount_str}{server_key}"
            calculated_signature = hashlib.sha512(raw_string.encode('utf-8')).hexdigest()

            if calculated_signature.lower() != signature_str.lower():
                logger.warning(
                    "[PAYMENT_AUDIT] invalid_signature | midtrans_order_id=%s | "
                    "status_code=%s | gross_amount=%s",
                    order_id_str, status_code_str, gross_amount_str,
                )
                return {"status": "error_handled", "message": "Invalid signature ignored"}

        # 2. Cari data payment dengan ROW-LEVEL LOCK (FOR UPDATE)
        #    mencegah dua webhook simultan memproses Payment row yang sama
        payment = None
        if pg_txn_id:
            result = await db.execute(
                select(Payment)
                .where(Payment.pg_transaction_id == pg_txn_id)
                .with_for_update()
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
                    .with_for_update()
                )
                payment = result.scalars().first()
            else:
                result = await db.execute(
                    select(Payment)
                    .join(Invoice, Payment.invoice_id == Invoice.id)
                    .where(Invoice.nomor_invoice == order_id_str)
                    .order_by(Payment.id.desc())
                    .with_for_update()
                )
                payment = result.scalars().first()

        if not payment:
            logger.warning(
                "[PAYMENT_AUDIT] payment_not_found | transaction_id=%s | "
                "midtrans_order_id=%s",
                pg_txn_id, order_id_str,
            )
            return {"status": "ok", "message": "Test notification received, dummy order ignored"}

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
        logger.error(f"Midtrans notification error: {e}", exc_info=True)
        return {"status": "error_handled"}


async def refresh_if_pending(db: AsyncSession, payment: Payment) -> Payment:
    """
    Check payment status via Midtrans API dan apply jika sudah settlement.
    
    Dilindungi oleh:
    - Idempotency check (hanya proses jika masih Pending)
    - State Machine (di dalam _apply_transaction_status)
    """
    if payment.payment_status != PaymentStatusEnum.pending:
        return payment  # Sudah terminal, skip
        
    url = f"{settings.midtrans_api_url}/{payment.pg_transaction_id}/status"
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
                # Commit DB terlebih dahulu sebelum memicu webhook Chatbot
                await _apply_transaction_status(db, payment, midtrans_payload, commit=True)
                await db.refresh(payment)
    except Exception as e:
        logger.error(f"Failed to refresh pending payment {payment.id} status: {e}")
        # Ignored and fallback to original payment object
    return payment


async def get_payments_by_order(db: AsyncSession, order_id: int) -> List[Payment]:
    return await payment_repo.get_payments_by_order_id(db, order_id)


async def process_refund(db: AsyncSession, order_id: int, reason: str) -> str:
    """
    Proses refund untuk transaksi yang berstatus Success.
    Mencoba memanggil API Refund/Cancel Midtrans, dengan fallback ke Refund Manual (offline)
    bila transaksi berupa VA / QRIS (HTTP 412) atau unsupported method.
    
    Mengembalikan mode refund:
    - 'auto': jika direct refund Midtrans berhasil (200/201)
    - 'manual': jika direct refund gagal (412 / unsupported / error) dan dicatat sebagai manual refund
    """
    payments = await get_payments_by_order(db, order_id)
    success_payments = [p for p in payments if p.payment_status == PaymentStatusEnum.success]
    
    if not success_payments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak ada pembayaran sukses yang bisa di-refund untuk pesanan ini."
        )

    refund_modes: list[str] = []

    for payment in success_payments:
        payment_refund_mode = "manual"

        # Panggil API Refund/Cancel Midtrans jika memungkinkan
        if payment.pg_transaction_id:
            url = f"{settings.midtrans_api_url}/{payment.pg_transaction_id}/refund"
            encoded_key = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
            headers = {
                "Authorization": f"Basic {encoded_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "refund_key": f"refund-{payment.id}-{int(datetime.now().timestamp())}",
                "reason": reason
            }
            
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    
                    # 1. Error 412: Transaksi VA / QRIS tidak mendukung Direct Refund via API
                    if response.status_code == 412:
                        logger.warning(
                            "[REFUND_AUDIT] midtrans_api_unsupported_412 | payment_id=%s | "
                            "method=%s | fallback=manual",
                            payment.id, payment.payment_method
                        )
                        payment_refund_mode = "manual"
                    elif response.status_code in [200, 201]:
                        res_json = response.json()
                        midtrans_status = str(res_json.get("status_code", ""))
                        if midtrans_status.startswith("2"):
                            payment_refund_mode = "auto"
                            logger.info(
                                "[REFUND_AUDIT] midtrans_api_success | payment_id=%s | "
                                "refund_key=%s | refund_mode=auto",
                                payment.id, payload["refund_key"]
                            )
                        elif midtrans_status == "412":
                            logger.warning(
                                "[REFUND_AUDIT] midtrans_api_unsupported_412_body | payment_id=%s | "
                                "message=%s | fallback=manual",
                                payment.id, res_json.get("status_message")
                            )
                            payment_refund_mode = "manual"
                        else:
                            logger.warning(
                                "[REFUND_AUDIT] midtrans_api_failed | payment_id=%s | "
                                "status_code=%s | message=%s | fallback=manual",
                                payment.id, midtrans_status, res_json.get("status_message")
                            )
                            payment_refund_mode = "manual"
                    else:
                        logger.warning(
                            "[REFUND_AUDIT] midtrans_api_non_2xx | payment_id=%s | "
                            "http_status=%s | fallback=manual",
                            payment.id, response.status_code
                        )
                        payment_refund_mode = "manual"
            except Exception as e:
                logger.error(f"Midtrans Refund API error for payment {payment.id}: {e}")
                logger.warning(f"[REFUND_AUDIT] fallback=manual for payment {payment.id}")
                payment_refund_mode = "manual"
        else:
            logger.info(f"[REFUND_AUDIT] No pg_transaction_id for payment {payment.id} | fallback=manual")
            payment_refund_mode = "manual"

        refund_modes.append(payment_refund_mode)

        # Update status DB (Offline / Fallback manual jika API gagal, atau Success jika API berhasil)
        # Dilakukan dengan commit=False agar seluruh payment diproses dalam satu atomic transaction
        fake_payload = {
            "transaction_status": "refund",
            "transaction_id": payment.pg_transaction_id,
            "order_id": str(order_id)
        }
        await _apply_transaction_status(db, payment, fake_payload, commit=False)

    # Commit seluruh mutasi status ke database terlebih dahulu
    await db.commit()

    # Evaluasi status mode refund keseluruhan
    overall_refund_mode = "auto" if refund_modes and all(m == "auto" for m in refund_modes) else "manual"

    # Sinyal Auto Refund (Poin 2.a):
    # Tembak webhook internal ke Chatbot HANYA jika panggilan API Refund Midtrans sukses (refund_mode == "auto")
    # Pemicu Webhook Chatbot dieksekusi SETELAH db.commit() berhasil dilakukan
    if overall_refund_mode == "auto":
        await notify_refund_status(order_id)

    return overall_refund_mode


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
    4. Update status order jika sesuai workflow (pending -> in_process).
    5. Commit ke database & kirim notifikasi chatbot.
    """
    order = await order_repo.get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan"
        )

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

    # Buat record transaksi pembayaran baru di database
    pg_txn_id = f"MANUAL-{order.id}-{int(time.time() * 1000)}-{secrets.token_hex(2).upper()}"
    payment_obj = Payment(
        invoice_id=invoice.id,
        pg_transaction_id=pg_txn_id,
        jumlah_bayar=amount,
        payment_method=payment_method,
        verified_by=verified_by,
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.final,
        settled_at=datetime.now(timezone.utc),
        notes=notes,
    )
    db.add(payment_obj)
    await db.flush()

    # Hitung total pembayaran sukses pada invoice
    sum_res = await db.execute(
        select(func.sum(Payment.jumlah_bayar))
        .where(
            Payment.invoice_id == invoice.id,
            Payment.payment_status == PaymentStatusEnum.success
        )
    )
    total_paid = sum_res.scalar() or Decimal("0.00")
    total_paid = Decimal(str(total_paid))

    if total_paid >= Decimal(str(invoice.total_tagihan)):
        invoice.status = InvoiceStatusEnum.paid
    else:
        invoice.status = InvoiceStatusEnum.partial

    # Perbarui status order jika sesuai workflow (pending -> in_process)
    if order.status == OrderStatusEnum.pending:
        order.status = OrderStatusEnum.in_process

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

