from decimal import Decimal
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.api.dependencies import get_auth_identity_optional_service_or_jwt, AuthIdentity
from app.services import payment_service
from app.repositories import order_repo, customer_repo
from app.models.payment import PaymentStatusEnum

router = APIRouter(
    tags=["Payments"],
    responses={
        401: {"description": "Unauthorized - Missing or invalid Service Key / Bearer JWT"},
        404: {"description": "Order or Payment not found"},
    },
)

class PaymentChargeRequest(BaseModel):
    order_id: int
    payment_method: str = Field(..., pattern="^(bank_transfer|qris)$")
    payment_type: str = Field(..., pattern="^(full|dp)$")
    amount: Decimal = Field(..., gt=0)

# 1. Endpoint POST /payments (secured via Service Key OR Buyer JWT)
@router.post("", status_code=status.HTTP_201_CREATED,
             summary="Charge payment via Midtrans Core API (Service Key / Buyer JWT)")
async def create_midtrans_payment(
    data: PaymentChargeRequest,
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db)
):
    """
    Buat request charge baru ke Midtrans Core API (headless).
    Dapat diakses oleh Chatbot (X-Service-Key) maupun Buyer (Bearer JWT).
    Mengembalikan data transaksi Midtrans (nomor VA atau QRIS string).
    """
    # Jika diakses oleh Buyer JWT, validasi kepemilikan order
    if auth.is_buyer:
        customer = await customer_repo.get_by_nomor_wa(db, auth.buyer.phone)
        order = await order_repo.get_order_by_id(db, data.order_id)
        if not order or not customer or order.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order tidak ditemukan"
            )

    return await payment_service.create_midtrans_charge(
        db,
        order_id=data.order_id,
        payment_method=data.payment_method,
        payment_type=data.payment_type,
        amount=data.amount
    )

# 2. Endpoint GET /payments/{order_id}/status (secured via Service Key OR Buyer JWT)
@router.get("/{order_id}/status",
            summary="Get latest payment status of an order")
async def get_order_payment_status(
    order_id: int,
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db)
):
    """
    Mengambil summary status pembayaran terakhir dari suatu order (Chatbot atau Buyer JWT).
    """
    if auth.is_buyer:
        customer = await customer_repo.get_by_nomor_wa(db, auth.buyer.phone)
        order = await order_repo.get_order_by_id(db, order_id)
        if not order or not customer or order.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order tidak ditemukan"
            )
    payments = await payment_service.get_payments_by_order(db, order_id)
    
    # Refresh status if any payment is pending
    refreshed_payments = []
    for p in payments:
        refreshed = await payment_service.refresh_if_pending(db, p)
        refreshed_payments.append(refreshed)
    payments = refreshed_payments

    order = await order_repo.get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")
        
    amount_paid = Decimal("0.00")
    for p in payments:
        if p.payment_status == PaymentStatusEnum.success:
            amount_paid += p.jumlah_bayar
            
    amount_due = Decimal("0.00")
    invoice_status = "unpaid"
    if order.invoice:
        amount_due = Decimal(str(order.invoice.total_tagihan)) - amount_paid
        invoice_status = order.invoice.status
        
    return {
        "order_id": order_id,
        "invoice_status": invoice_status,
        "amount_paid": amount_paid,
        "amount_due": amount_due,
        "payments": [
            {
                "id": p.id,
                "pg_transaction_id": p.pg_transaction_id,
                "jumlah_bayar": p.jumlah_bayar,
                "payment_method": p.payment_method,
                "payment_status": p.payment_status,
                "payment_type": p.payment_type,
                "va_number": p.va_number,
                "qris_url": p.qris_url,
                "created_at": p.created_at
            } for p in payments
        ]
    }

# 3. Endpoint POST /payments/notify (PUBLIC - webhook)
@router.post("/notify", status_code=status.HTTP_200_OK,
             summary="Midtrans Webhook Notification Listener")
async def midtrans_notification(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook notification endpoint yang ditembak oleh server Midtrans secara otomatis.
    Memproses update status transaksi berdasarkan signature Midtrans.
    """
    await payment_service.process_midtrans_webhook(db, payload)
    return {"status": "ok"}
