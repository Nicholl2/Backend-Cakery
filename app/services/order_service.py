from datetime import datetime
from decimal import Decimal
from typing import Optional
import logging
import httpx

from fastapi import HTTPException, status
from app.core.config import settings

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem, Invoice, MetodePengirimanEnum, OrderStatusEnum, InvoiceStatusEnum
from app.models.product import Product
from app.models.payment import Payment, PaymentStatusEnum
from app.models.stock_item import StockItem
from app.models.recipe import Recipe
from app.repositories import order_repo
from app.utils.phone import normalize_phone


from app.repositories import customer_repo
from app.models.buyer import Buyer
from app.schemas.order import BuyerOrderCreate, CustomOrderCreate


async def create_new_order(
    db: AsyncSession,
    customer_id: int,
    items: list[dict],          # [{"product_id": int, "jumlah": int}, ...]
    metode_pengiriman: str,
    created_via: str = "chatbot",
    notes: Optional[str] = None,
    due_date: Optional[datetime] = None,
    payment_method_preference: Optional[str] = None,
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
        stock_item_requirements = {}

        for item in items:
            result = await db.execute(
                select(Product)
                .where(Product.id == item["product_id"])
                .options(selectinload(Product.recipes).selectinload(Recipe.stock_item))
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
            if product.minimum_order is not None and jumlah < product.minimum_order:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Jumlah pesanan untuk '{product.nama_produk}' minimal {product.minimum_order} pcs.",
                )

            subtotal = Decimal(str(product.harga_jual)) * jumlah
            total_harga_pesanan += subtotal

            item_data_list.append({
                "product_id": product.id,
                "jumlah": jumlah,
                "hpp_snapshot": Decimal(str(product.hpp_total or 0)),
                "subtotal": subtotal,
                "custom_decoration_charge": Decimal(str(item.get("custom_decoration_charge", "0.00"))),
            })

            # Kumpulkan kebutuhan bahan baku untuk product ini
            if product.recipes:
                for recipe in product.recipes:
                    stock_item = recipe.stock_item
                    if not stock_item:
                        continue
                    
                    qty_needed = Decimal(str(jumlah)) * Decimal(str(recipe.jumlah_dibutuhkan))
                    if stock_item.id not in stock_item_requirements:
                        stock_item_requirements[stock_item.id] = {
                            "total_needed": Decimal("0"),
                            "product_name": product.nama_produk,
                            "stock_item_obj": stock_item
                        }
                    stock_item_requirements[stock_item.id]["total_needed"] += qty_needed

        # ── 3. Validasi & Kurangi Stok (Optimistic Locking + Auto-Retry) ────
        MAX_STOCK_RETRY = 3

        for stock_id, req in stock_item_requirements.items():
            total_needed = req["total_needed"]
            product_name = req["product_name"]

            for attempt in range(1, MAX_STOCK_RETRY + 1):
                # Re-fetch stock item untuk fresh version pada retry
                if attempt > 1:
                    fresh_result = await db.execute(
                        select(StockItem).where(StockItem.id == stock_id)
                    )
                    stock_item = fresh_result.scalars().first()
                    if not stock_item:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Bahan baku tidak ditemukan saat retry.",
                        )
                else:
                    stock_item = req["stock_item_obj"]

                # Validasi stok bahan baku
                if stock_item.stok_tersedia < total_needed:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Stok bahan baku tidak mencukupi untuk memproses pesanan {product_name}",
                    )

                # Kurangi stok & terapkan Optimistic Locking dengan filter version
                stmt = (
                    update(StockItem)
                    .where(StockItem.id == stock_id, StockItem.version == stock_item.version)
                    .values(
                        stok_tersedia=StockItem.stok_tersedia - total_needed,
                        version=StockItem.version + 1
                    )
                )
                res = await db.execute(stmt)
                if res.rowcount > 0:
                    break  # Berhasil, lanjut ke stock item berikutnya

                # Conflict: versi berubah oleh transaksi lain
                if attempt == MAX_STOCK_RETRY:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Terjadi kegagalan validasi stok karena transaksi bersamaan. Silakan coba lagi.",
                    )
                logger.warning(
                    "[STOCK_RETRY] Optimistic lock conflict on stock_item %s, "
                    "attempt %s/%s, retrying...",
                    stock_id, attempt, MAX_STOCK_RETRY,
                )

        # ── 4. Buat Order ────────────────────────────────────────────────────
        order_obj = Order(
            customer_id=customer_id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum(metode_pengiriman),
            total_harga_pesanan=total_harga_pesanan,
            created_via=created_via,
            notes=notes,
            due_date=due_date,
            payment_method_preference=payment_method_preference,
        )
        await order_repo.create_order(db, order_obj)  # flush → dapat order.id

        # ── 5. Buat OrderItems ───────────────────────────────────────────────
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

        # ── 6. Generate nomor invoice & buat Invoice ─────────────────────────
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

        # ── 7. Commit & return dengan relasi ter-load ─────────────────────────
        await db.commit()
        order = await order_repo.get_order_with_details(db, order_obj.id)
        return await _attach_payment_amounts(db, order)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal membuat order: {str(e)}",
        )


async def create_custom_order(
    db: AsyncSession,
    data: CustomOrderCreate,
) -> Order:
    """
    Membuat pesanan kustom buatan seller tanpa master produk:
    1. Upsert Customer berdasarkan customer_phone dan customer_name.
    2. Hitung total pesanan dari daftar item kustom (price * qty + custom_decoration_charge).
    3. Bypass pemotongan stok bahan baku/recipe.
    4. Buat Order & OrderItems dengan product_id = None dan custom_product_name.
    5. Buat Invoice otomatis dengan status 'unpaid'.
    """
    try:
        phone_norm = normalize_phone(data.customer_phone)
        customer, _ = await customer_repo.upsert(
            db,
            nomor_wa=phone_norm,
            nama=data.customer_name,
            alamat=data.customer_address,
        )
        await db.flush()

        # Cek tagihan aktif untuk customer ini
        has_active = await order_repo.check_active_unpaid_order(db, customer.id)
        if has_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer masih memiliki tagihan aktif yang belum lunas.",
            )

        # Kalkulasi item & total
        total_harga_pesanan = Decimal("0.00")
        item_objects = []

        for item in data.items:
            qty = item.qty
            price = Decimal(str(item.price))
            dec_charge = Decimal(str(item.custom_decoration_charge))
            subtotal = (price * qty) + dec_charge
            total_harga_pesanan += subtotal

            item_objects.append({
                "custom_product_name": item.custom_product_name,
                "jumlah": qty,
                "custom_decoration_charge": dec_charge,
                "subtotal": subtotal,
                "hpp_snapshot": Decimal("0.00"),
            })

        # Buat Order (created_via = "seller")
        order_obj = Order(
            customer_id=customer.id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum(data.metode_pengiriman),
            total_harga_pesanan=total_harga_pesanan,
            created_via="seller",
            notes=data.notes,
            due_date=data.due_date,
            payment_method_preference=data.payment_method_preference,
        )
        await order_repo.create_order(db, order_obj)

        # Buat OrderItems kustom tanpa product_id
        for item_data in item_objects:
            await order_repo.create_order_item(
                db,
                OrderItem(
                    order_id=order_obj.id,
                    product_id=None,
                    custom_product_name=item_data["custom_product_name"],
                    jumlah=item_data["jumlah"],
                    custom_decoration_charge=item_data["custom_decoration_charge"],
                    subtotal=item_data["subtotal"],
                    hpp_snapshot=item_data["hpp_snapshot"],
                ),
            )

        # Buat Invoice otomatis
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

        await db.commit()
        order = await order_repo.get_order_with_details(db, order_obj.id)
        return await _attach_payment_amounts(db, order)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal membuat custom order: {str(e)}",
        )


async def _attach_payment_amounts(db: AsyncSession, order: Order) -> Order:
    if not order:
        return order
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


async def get_seller_orders(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
) -> list[Order]:
    orders = await order_repo.get_all_orders(db, limit=limit, offset=offset, status=status)
    for o in orders:
        await _attach_payment_amounts(db, o)
    return orders


async def get_seller_order_by_id(db: AsyncSession, order_id: int) -> Order:
    order = await order_repo.get_order_with_details(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
    return await _attach_payment_amounts(db, order)


async def get_customer_latest_order(db: AsyncSession, nomor_wa: str) -> Order:
    nomor_wa = normalize_phone(nomor_wa)
    order = await order_repo.get_latest_order_by_wa(db, nomor_wa)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pelanggan belum memiliki pesanan",
        )
    return await _attach_payment_amounts(db, order)


async def get_or_create_customer_for_buyer(db: AsyncSession, buyer: Buyer):
    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if not customer:
        customer, _ = await customer_repo.upsert(
            db,
            nomor_wa=buyer.phone,
            nama=buyer.name,
            alamat=None,
        )
        await db.commit()
        await db.refresh(customer)
    return customer


async def get_buyer_orders(db: AsyncSession, buyer: Buyer) -> list[Order]:
    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if not customer:
        return []
    orders = await order_repo.get_orders_by_customer_id(db, customer.id)
    for o in orders:
        await _attach_payment_amounts(db, o)
    return orders


async def get_buyer_order_by_id(db: AsyncSession, buyer: Buyer, order_id: int) -> Order:
    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
    order = await order_repo.get_order_by_id_and_customer(db, order_id, customer.id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
    await _attach_payment_amounts(db, order)
    return order


async def create_buyer_order(db: AsyncSession, buyer: Buyer, data: BuyerOrderCreate) -> Order:
    customer = await get_or_create_customer_for_buyer(db, buyer)
    order = await create_new_order(
        db=db,
        customer_id=customer.id,
        items=[item.model_dump() for item in data.items],
        metode_pengiriman=data.metode_pengiriman,
        created_via=data.created_via,
        notes=data.notes,
        due_date=data.due_date,
        payment_method_preference=data.payment_method_preference,
    )
    await _attach_payment_amounts(db, order)
    return order


async def cancel_order_by_customer(db: AsyncSession, order_id: int) -> dict:
    try:
        # Lock Order row untuk mencegah double-cancel bersamaan
        result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.invoice),
                selectinload(Order.order_items)
                .selectinload(OrderItem.product)
                .selectinload(Product.recipes)
                .selectinload(Recipe.stock_item)
            )
            .with_for_update()
        )
        order = result.scalars().first()
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
            
        # Restore stock items
        stock_item_returns = {}
        for item in order.order_items:
            if item.product and item.product.recipes:
                for recipe in item.product.recipes:
                    stock_item = recipe.stock_item
                    if not stock_item:
                        continue
                    kembalikan = Decimal(str(recipe.jumlah_dibutuhkan)) * Decimal(str(item.jumlah))
                    if stock_item.id not in stock_item_returns:
                        stock_item_returns[stock_item.id] = {
                            "qty_return": Decimal("0.00"),
                            "stock_item_obj": stock_item
                        }
                    stock_item_returns[stock_item.id]["qty_return"] += kembalikan

        # Execute updates with optimistic locking check
        for stock_id, data in stock_item_returns.items():
            stock_item = data["stock_item_obj"]
            qty_return = data["qty_return"]
            
            stmt = (
                update(StockItem)
                .where(StockItem.id == stock_id, StockItem.version == stock_item.version)
                .values(
                    stok_tersedia=StockItem.stok_tersedia + qty_return,
                    version=StockItem.version + 1
                )
            )
            res = await db.execute(stmt)
            if res.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Terjadi kegagalan pemulihan stok karena transaksi bersamaan. Silakan coba lagi.",
                )

        order.status = OrderStatusEnum.cancelled
        await db.commit()
        return {"status": "success", "message": "Pesanan berhasil dibatalkan"}

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal membatalkan order: {str(e)}",
        )


async def update_order_status(db: AsyncSession, order_id: int, new_status: str) -> Order:
    from app.core.state_machine import is_valid_order_transition

    # Lock Order row untuk mencegah concurrent status update (double-cancel, dll)
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.invoice).selectinload(Invoice.payments),
            selectinload(Order.order_items)
            .selectinload(OrderItem.product)
            .selectinload(Product.recipes)
            .selectinload(Recipe.stock_item)
        )
        .with_for_update()
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )

    # State Machine validation
    try:
        new_status_enum = OrderStatusEnum(new_status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Status '{new_status}' tidak valid.",
        )

    if not is_valid_order_transition(order.status, new_status_enum):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Transisi status tidak valid: '{order.status.value}' → '{new_status}'. "
                   f"Pesanan sudah dalam status '{order.status.value}'.",
        )

    # Jika status diubah menjadi 'cancelled' dari status sebelumnya yang bukan cancelled
    if new_status == OrderStatusEnum.cancelled.value and order.status != OrderStatusEnum.cancelled:
        await _rollback_order_stock(db, order)

    order.status = new_status
    await db.commit()

    # Re-query order with details to avoid lazy loading issues
    order_refetched = await order_repo.get_order_with_details(db, order_id)
    await _attach_payment_amounts(db, order_refetched)

    if new_status == "ready":
        try:
            url = f"{settings.chatbot_url}/webhook/internal/orders/{order_id}/ready"
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={},
                    headers={"X-Internal-Key": settings.chatbot_internal_key}
                )
        except Exception as e:
            logger.error(f"Failed to send webhook push notification for order {order_id}: {e}")
            
    return order_refetched


async def _rollback_order_stock(db: AsyncSession, order: Order) -> None:
    from app.models.stock_item import StockItem
    from sqlalchemy import update
    
    stock_item_returns = {}
    for item in order.order_items:
        if item.product and item.product.recipes:
            for recipe in item.product.recipes:
                stock_item = recipe.stock_item
                if not stock_item:
                    continue
                kembalikan = Decimal(str(recipe.jumlah_dibutuhkan)) * Decimal(str(item.jumlah))
                if stock_item.id not in stock_item_returns:
                    stock_item_returns[stock_item.id] = {
                        "qty_return": Decimal("0.00"),
                        "stock_item_obj": stock_item
                    }
                stock_item_returns[stock_item.id]["qty_return"] += kembalikan

    for stock_id, s_data in stock_item_returns.items():
        stock_item = s_data["stock_item_obj"]
        qty_return = s_data["qty_return"]
        
        stmt = (
            update(StockItem)
            .where(StockItem.id == stock_id, StockItem.version == stock_item.version)
            .values(
                stok_tersedia=StockItem.stok_tersedia + qty_return,
                version=StockItem.version + 1
            )
        )
        res = await db.execute(stmt)
        if res.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Terjadi kegagalan pemulihan stok karena transaksi bersamaan. Silakan coba lagi.",
            )


async def cancel_and_refund_order(db: AsyncSession, order_id: int, reason: str) -> Order:
    from app.services.payment_service import process_refund
    
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.invoice).selectinload(Invoice.payments),
            selectinload(Order.order_items)
            .selectinload(OrderItem.product)
            .selectinload(Product.recipes)
            .selectinload(Recipe.stock_item)
        )
        .with_for_update()
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )
        
    from app.core.state_machine import is_order_terminal
    if is_order_terminal(order.status):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pesanan dalam status '{order.status.value}' tidak dapat di-refund."
        )
        
    if not order.invoice or order.invoice.status in [InvoiceStatusEnum.unpaid, InvoiceStatusEnum.refunded]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya pesanan dengan status pembayaran DP/Lunas yang dapat diproses refund. Gunakan cancel biasa."
        )

    # Call payment_service to do the API request & status change
    await process_refund(db, order_id, reason)
    
    # Re-fetch after updates
    order_refetched = await order_repo.get_order_with_details(db, order_id)
    return await _attach_payment_amounts(db, order_refetched)