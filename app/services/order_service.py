from datetime import datetime
from decimal import Decimal
import logging
import httpx

from fastapi import HTTPException, status
from app.core.config import settings

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.order import Order, OrderItem, Invoice, MetodePengirimanEnum, OrderStatusEnum, InvoiceStatusEnum
from app.models.product import Product
from app.models.payment import Payment, PaymentStatusEnum
from app.repositories import order_repo


async def create_new_order(
    db: AsyncSession,
    customer_id: int,
    items: list[dict],          # [{"product_id": int, "jumlah": int}, ...]
    metode_pengiriman: str,
    created_via: str = "chatbot",
) -> Order:
    try:
        # ── 1. Cek tagihan aktif ─────────────────────────────────────────────
        has_active = await order_repo.check_active_unpaid_order(db, customer_id)
        if has_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer masih memiliki tagihan aktif yang belum lunas.",
            )

        # ── 2. Validasi & kalkulasi item ─────────────────────────────────────
        total_harga_pesanan = Decimal("0.00")
        item_data_list: list[dict] = []

        for item in items:
            result = await db.execute(
                select(Product).where(Product.id == item["product_id"])
            )
            product = result.scalars().first()

            if not product:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Produk ID {item['product_id']} tidak ditemukan.",
                )
            if not product.is_active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Produk '{product.nama_produk}' tidak aktif.",
                )
            if not product.harga_jual:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Produk '{product.nama_produk}' belum memiliki harga jual.",
                )

            jumlah = item["jumlah"]
            subtotal = Decimal(str(product.harga_jual)) * jumlah
            total_harga_pesanan += subtotal

            item_data_list.append({
                "product_id": product.id,
                "jumlah": jumlah,
                "hpp_snapshot": Decimal(str(product.hpp_total or 0)),
                "subtotal": subtotal,
                "custom_decoration_charge": Decimal(str(item.get("custom_decoration_charge", "0.00"))),
            })

        # ── 3. Buat Order ────────────────────────────────────────────────────
        order_obj = Order(
            customer_id=customer_id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum(metode_pengiriman),
            total_harga_pesanan=total_harga_pesanan,
            created_via=created_via,
        )
        await order_repo.create_order(db, order_obj)  # flush → dapat order.id

        # ── 4. Buat OrderItems ───────────────────────────────────────────────
        for d in item_data_list:
            await order_repo.create_order_item(
                db,
                OrderItem(
                    order_id=order_obj.id,
                    product_id=d["product_id"],
                    jumlah=d["jumlah"],
                    custom_decoration_charge=d["custom_decoration_charge"],
                    subtotal=d["subtotal"],
                    hpp_snapshot=d["hpp_snapshot"],
                ),
            )

        # ── 5. Generate nomor invoice & buat Invoice ─────────────────────────
        nomor_invoice = f"INV-{datetime.now().strftime('%Y%m%d')}-{order_obj.id}"
        await order_repo.create_invoice(
            db,
            Invoice(
                order_id=order_obj.id,
                nomor_invoice=nomor_invoice,
                total_tagihan=total_harga_pesanan,
                status=InvoiceStatusEnum.unpaid,
            ),
        )

        # ── 6. Commit & return dengan relasi ter-load ─────────────────────────
        await db.commit()
        return await order_repo.get_order_with_details(db, order_obj.id)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal membuat order: {str(e)}",
        )


async def get_customer_latest_order(db: AsyncSession, nomor_wa: str) -> Order:
    order = await order_repo.get_latest_order_by_wa(db, nomor_wa)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pelanggan belum memiliki pesanan",
        )
    
    amount_paid = Decimal("0.00")
    if order.invoice:
        payment_query = await db.execute(
            select(func.sum(Payment.jumlah_bayar))
            .where(
                Payment.invoice_id == order.invoice.id,
                Payment.payment_status == PaymentStatusEnum.success,
            )
        )
        sum_result = payment_query.scalar()
        if sum_result is not None:
            amount_paid = Decimal(str(sum_result))
            
    if order.invoice:
        amount_due = Decimal(str(order.invoice.total_tagihan)) - amount_paid
    else:
        amount_due = Decimal("0.00")
        
    order.amount_paid = amount_paid
    order.amount_due = amount_due
    
    return order


async def cancel_order_by_customer(db: AsyncSession, order_id: int) -> dict:
    order = await order_repo.get_order_by_id(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
        
    if not order.invoice or order.invoice.status != InvoiceStatusEnum.unpaid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pesanan yang sudah dibayar atau dicicil tidak dapat dibatalkan secara otomatis",
        )
        
    order.status = OrderStatusEnum.cancelled
    await db.commit()
    
    return {"status": "success", "message": "Pesanan berhasil dibatalkan"}


async def update_order_status(db: AsyncSession, order_id: int, new_status: str) -> Order:
    order = await order_repo.update_order_status(db, order_id, new_status)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
        
    await db.commit()
    
    if new_status == "ready":
        try:
            url = f"{settings.chatbot_url}/webhook/internal/orders/{order.id}/ready"
            async with httpx.AsyncClient() as client:
                await client.post(url, json={})
        except Exception as e:
            logger.error(f"Failed to send webhook push notification for order {order.id}: {e}")
            
    return order