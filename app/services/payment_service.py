import base64
import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import settings
from app.models.order import Order, Invoice, OrderStatusEnum, InvoiceStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.repositories import order_repo, payment_repo

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
    
    # 2. Buat request payload HTTP POST ke Midtrans API Charge (/charge)
    # Order ID di Midtrans dikombinasikan dengan suffix agar unik
    order_id_midtrans = f"{invoice.nomor_invoice}-PAY-{int(datetime.now().timestamp())}"
    
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
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            res_json = response.json()
    except Exception as e:
        logger.error(f"Failed to charge via Midtrans: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gagal memproses pembayaran ke Midtrans: {str(e)}"
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
    
    return {
        "payment_id": payment_obj.id,
        "pg_transaction_id": pg_transaction_id,
        "va_number": va_number,
        "qris_url": qris_url,
        "status": payment_obj.payment_status,
        "midtrans_response": res_json
    }

async def _apply_transaction_status(db: AsyncSession, payment: Payment, payload: dict) -> None:
    # 3. Lakukan mapping status Midtrans ke PaymentStatusEnum
    txn_status = payload.get("transaction_status")
    status_map = {
        "settlement": PaymentStatusEnum.success,
        "capture": PaymentStatusEnum.success,
        "pending": PaymentStatusEnum.pending,
        "deny": PaymentStatusEnum.failed,
        "cancel": PaymentStatusEnum.failed,
        "expire": PaymentStatusEnum.failed
    }
    
    new_status = status_map.get(txn_status, PaymentStatusEnum.pending)
    payment.payment_status = new_status
    await db.flush()
    
    # 4. ATURAN OTOMASI: Jika status payment berubah menjadi 'Success'
    if new_status == PaymentStatusEnum.success:
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
            
            # Update status invoice dan order status
            if total_success >= Decimal(str(invoice.total_tagihan)):
                invoice.status = InvoiceStatusEnum.paid
                
                # Ubah status pesanan induk menjadi 'in_process'
                order_res = await db.execute(
                    select(Order).where(Order.id == invoice.order_id)
                )
                order = order_res.scalars().first()
                if order:
                    order.status = OrderStatusEnum.in_process
            else:
                invoice.status = InvoiceStatusEnum.partial


async def process_midtrans_webhook(db: AsyncSession, payload: dict) -> dict:
    order_id = payload.get("order_id")
    status_code = payload.get("status_code")
    gross_amount = payload.get("gross_amount")
    signature_from_payload = payload.get("signature_key")
    
    # 1. Validasi integritas request menggunakan SHA512 Signature Key
    raw_string = f"{order_id}{status_code}{gross_amount}{settings.midtrans_server_key}"
    calculated_signature = hashlib.sha512(raw_string.encode('utf-8')).hexdigest()
    
    if calculated_signature != signature_from_payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Signature"
        )
        
    pg_transaction_id = payload.get("transaction_id")
    
    # 2. Cari data payment berdasarkan pg_transaction_id atau parsing order_id
    payment = await payment_repo.get_payment_by_pg_id(db, pg_transaction_id)
    if not payment:
        if order_id and "-PAY-" in order_id:
            inv_num = order_id.split("-PAY-")[0]
            result = await db.execute(
                select(Payment)
                .join(Invoice, Payment.invoice_id == Invoice.id)
                .where(Invoice.nomor_invoice == inv_num)
                .order_by(Payment.id.desc())
            )
            payment = result.scalars().first()
            
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data pembayaran tidak ditemukan"
        )
        
    await _apply_transaction_status(db, payment, payload)
    await db.commit()
    return {"status": "success", "payment_status": payment.payment_status}


async def refresh_if_pending(db: AsyncSession, payment: Payment) -> Payment:
    """Check payment status via Midtrans API and apply if success"""
    if payment.payment_status == PaymentStatusEnum.pending:
        url = f"{settings.midtrans_api_url}/{payment.pg_transaction_id}/status"
        encoded_key = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded_key}",
            "Accept": "application/json"
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    payload = response.json()
                    await _apply_transaction_status(db, payment, payload)
                    await db.commit()
                    await db.refresh(payment)
        except Exception as e:
            logger.error(f"Failed to refresh pending payment {payment.id} status: {e}")
            # Ignored and fallback to original payment object
    return payment


async def get_payments_by_order(db: AsyncSession, order_id: int) -> List[Payment]:
    return await payment_repo.get_payments_by_order_id(db, order_id)
