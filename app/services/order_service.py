from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.order import Order, OrderItem, Invoice, MetodePengirimanEnum, OrderStatusEnum, InvoiceStatusEnum
from app.models.product import Product
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