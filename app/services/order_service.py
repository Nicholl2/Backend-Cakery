from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal

MAX_ORDER_AMOUNT = Decimal("50000000")
from typing import Optional
import logging
import httpx
import secrets
import hashlib
import hmac
import json
import re

from fastapi import HTTPException, status
from app.core.config import settings

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, cast, String
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem, OrderAccessory, Invoice, MetodePengirimanEnum, OrderStatusEnum, InvoiceStatusEnum
from app.models.product import Product
from app.models.payment import Payment, PaymentStatusEnum
from app.models.stock_item import StockItem, StockMovement
from app.models.recipe import Recipe
from app.repositories import order_repo
from app.utils.phone import normalize_phone


from app.repositories import customer_repo
from app.models.customer import Customer
from app.models.buyer import Buyer
from app.schemas.order import BuyerOrderCreate, CustomOrderCreate


_ORDER_IDEMPOTENCY_MARKER_V1 = re.compile(
    r"\x1eorder-idempotency-v1:([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64})\x1e$"
)
_ORDER_IDEMPOTENCY_MARKER_V2 = re.compile(
    r"\x1eorder-idempotency-v2:([a-zA-Z0-9:_-]+):([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64})\x1e$"
)


def _order_idempotency_signature(scope: str, key_hash: str, fingerprint: str, version: int = 1) -> str:
    prefix = f"order-idempotency-v{version}"
    message = f"{prefix}:{scope}:{key_hash}:{fingerprint}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def _read_order_idempotency_marker(
    notes: Optional[str], customer_id: int, required_scope: Optional[str] = None,
):
    if not notes:
        return None
    match = _ORDER_IDEMPOTENCY_MARKER_V2.search(notes)
    if match:
        marker_scope, key_hash, fingerprint, signature = match.groups()
        if required_scope is not None and marker_scope != required_scope:
            return None
        expected = _order_idempotency_signature(marker_scope, key_hash, fingerprint, version=2)
    else:
        match = _ORDER_IDEMPOTENCY_MARKER_V1.search(notes)
        if not match:
            return None
        key_hash, fingerprint, signature = match.groups()
        marker_scope = str(customer_id)
        if required_scope is not None and marker_scope != required_scope:
            return None
        expected = _order_idempotency_signature(marker_scope, key_hash, fingerprint)
    if not hmac.compare_digest(signature, expected):
        return None
    return key_hash, fingerprint, notes[:match.start()], marker_scope


async def create_new_order(
    db: AsyncSession,
    customer_id: int,
    items: list[dict],          # [{"product_id": int, "jumlah": int}, ...]
    metode_pengiriman: str,
    created_via: str = "chatbot",
    notes: Optional[str] = None,
    due_date: Optional[datetime] = None,
    fulfillment_date: Optional[datetime] = None,
    payment_method_preference: Optional[str] = None,
    email_seller_on_commit: bool = True,
    actor_buyer: Optional[Buyer] = None,
    idempotency_key: Optional[str] = None,
    idempotency_scope: Optional[str] = None,
) -> Order:
    try:
        # ── 1. Cek tagihan aktif ─────────────────────────────────────────────
        await order_repo.lock_customer_for_order_creation(db, customer_id)

        # Persist an authenticated marker in the existing notes column; the
        # HMAC prevents ordinary user notes from impersonating idempotency data.
        fingerprint_payload = {
            "items": items,
            "metode_pengiriman": metode_pengiriman,
            "created_via": created_via,
            "notes": notes,
            "due_date": due_date.isoformat() if due_date else None,
            "fulfillment_date": fulfillment_date.isoformat() if fulfillment_date else None,
            "payment_method_preference": payment_method_preference,
        }
        if idempotency_scope is None:
            fingerprint_payload["customer_id"] = customer_id
        request_fingerprint = hashlib.sha256(json.dumps(
            fingerprint_payload, sort_keys=True, default=str, separators=(",", ":")
        ).encode()).hexdigest()
        key_marker = None
        if idempotency_key:
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
            if idempotency_scope is None:
                signature = _order_idempotency_signature(str(customer_id), key_hash, request_fingerprint)
                key_marker = f"\x1eorder-idempotency-v1:{key_hash}:{request_fingerprint}:{signature}\x1e"
                previous_query = select(Order).where(Order.customer_id == customer_id)
            else:
                signature = _order_idempotency_signature(
                    idempotency_scope, key_hash, request_fingerprint, version=2
                )
                key_marker = (f"\x1eorder-idempotency-v2:{idempotency_scope}:"
                              f"{key_hash}:{request_fingerprint}:{signature}\x1e")
                previous_query = select(Order)
            previous_result = await db.execute(
                previous_query.where(Order.notes.contains(key_hash)).order_by(Order.id.desc())
            )
            for previous in previous_result.scalars().all():
                marker = _read_order_idempotency_marker(
                    previous.notes, previous.customer_id,
                    required_scope=idempotency_scope if idempotency_scope is not None else str(customer_id),
                )
                if not marker or marker[0] != key_hash:
                    continue
                if marker[1] != request_fingerprint:
                    raise HTTPException(status_code=409, detail="Idempotency-Key sudah digunakan untuk request berbeda.")
                order = await order_repo.get_order_with_details(db, previous.id)
                return await _attach_payment_amounts(db, order)

        has_active = await order_repo.check_active_unpaid_order(db, customer_id)
        if has_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Customer masih memiliki tagihan aktif yang belum lunas.",
            )

        # ── 1b. Validasi tanggal fulfillment (defense-in-depth) ──────────────
        if fulfillment_date is not None:
            from app.utils.business_date import validate_fulfillment_date as _validate_date
            try:
                _validate_date(fulfillment_date)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(e),
                )

        # ── 2. Validasi & kalkulasi item ─────────────────────────────────────
        total_harga_pesanan = Decimal("0.00")
        item_data_list: list[dict] = []
        stock_item_requirements = {}
        stock_checked_products: dict[int, dict] = {}

        for item in items:
            result = await db.execute(
                select(Product)
                .where(Product.id == item["product_id"])
                .options(selectinload(Product.recipes).selectinload(Recipe.stock_item))
                .execution_options(populate_existing=True)
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

            # Validate catalog flags now; physical stock is checked against the
            # refreshed, locked stock rows below.
            if not product.is_available:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Produk '{product.nama_produk}' sedang tidak tersedia.",
                )
            if not product.recipes:
                raise HTTPException(status_code=400, detail=f"Stok produk '{product.nama_produk}' sedang habis.")
            stock_checked_products[product.id] = {
                "product": product,
                "jumlah": stock_checked_products.get(product.id, {}).get("jumlah", 0) + jumlah,
            }

            decoration_charge = Decimal(str(item.get("custom_decoration_charge", "0.00")))
            subtotal = (Decimal(str(product.harga_jual)) * jumlah) + decoration_charge
            total_harga_pesanan += subtotal

            item_data_list.append({
                "product_id": product.id,
                "product_name_snapshot": product.nama_produk,
                "unit_price_snapshot": Decimal(str(product.harga_jual)),
                "jumlah": jumlah,
                "hpp_snapshot": Decimal(str(product.hpp_total or 0)),
                "subtotal": subtotal,
                "custom_decoration_charge": decoration_charge,
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

        # ── 2b. Spending Cap — proteksi transaksi anomali ────────────────────
        if total_harga_pesanan > MAX_ORDER_AMOUNT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Total pesanan Rp {total_harga_pesanan:,.0f} melebihi batas maksimal Rp {MAX_ORDER_AMOUNT:,.0f} per order.",
            )

        # ── 3. Validasi & Kurangi Stok (Row-Level Locking SELECT ... FOR UPDATE) ────
        sorted_stock_ids = sorted(stock_item_requirements.keys())
        if sorted_stock_ids:
            res = await db.execute(
                select(StockItem)
                .where(StockItem.id.in_(sorted_stock_ids))
                .order_by(StockItem.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            locked_stock_items = {s.id: s for s in res.scalars().all()}

            # Preserve product-level stock errors, but calculate availability
            # from the refreshed locked stock snapshot rather than stale ORM data.
            for product_data in stock_checked_products.values():
                product = product_data["product"]
                available = None
                for recipe in product.recipes:
                    recipe_qty = Decimal(str(recipe.jumlah_dibutuhkan or 0))
                    if not recipe_qty.is_finite() or recipe_qty <= 0:
                        continue
                    stock_item = locked_stock_items.get(recipe.stock_item_id)
                    if stock_item is None:
                        continue  # The missing stock row receives a 422 below.
                    recipe_available = int(Decimal(str(stock_item.stok_tersedia or 0)) // recipe_qty)
                    available = recipe_available if available is None else min(available, recipe_available)

                if available is None or available <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Stok produk '{product.nama_produk}' sedang habis.",
                    )
                if product_data["jumlah"] > available:
                    raise HTTPException(
                        status_code=400,
                        detail=(f"Jumlah pesanan ({product_data['jumlah']}) melebihi stok yang tersedia "
                                f"({available}) untuk produk '{product.nama_produk}'."),
                    )

            for stock_id in sorted_stock_ids:
                req = stock_item_requirements[stock_id]
                total_needed = req["total_needed"]
                product_name = req["product_name"]

                if not total_needed.is_finite() or total_needed <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Jumlah kebutuhan bahan baku tidak valid untuk {product_name}.",
                    )

                stock_item = locked_stock_items.get(stock_id)
                if not stock_item:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Bahan baku ID {stock_id} tidak ditemukan.",
                    )

                # Conditional UPDATE is the final oversell guard. FOR UPDATE
                # serializes PostgreSQL writers; this predicate also protects
                # backends that ignore row locks (including SQLite tests).
                deduction = await db.execute(
                    update(StockItem)
                    .where(StockItem.id == stock_id, StockItem.stok_tersedia >= total_needed)
                    .values(
                        stok_tersedia=StockItem.stok_tersedia - total_needed,
                        version=StockItem.version + 1,
                    )
                    .execution_options(synchronize_session=False)
                )
                if deduction.rowcount != 1:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Stok bahan baku tidak mencukupi untuk memproses pesanan {product_name}",
                    )
                await db.refresh(stock_item)
                req["stock_item_obj"] = stock_item

        # ── 4. Buat Order ────────────────────────────────────────────────────
        created_at = datetime.now(timezone.utc)
        order_obj = Order(
            customer_id=customer_id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum(metode_pengiriman),
            total_harga_pesanan=total_harga_pesanan,
            created_via=created_via,
            notes=(notes or "") + key_marker if key_marker else notes,
            due_date=due_date,
            fulfillment_date=fulfillment_date,
            created_at=created_at,
            payment_expires_at=created_at + timedelta(minutes=settings.order_unpaid_timeout_minutes),
            payment_method_preference=payment_method_preference,
        )
        await order_repo.create_order(db, order_obj)  # flush → dapat order.id

        for stock_id, req in stock_item_requirements.items():
            db.add(StockMovement(stock_item_id=stock_id, movement_type="ORDER",
                                 quantity=-req["total_needed"], unit_cost=Decimal(str(req["stock_item_obj"].harga_per_satuan or 0)),
                                 reference_type="order", reference_id=order_obj.id,
                                 reason="Pemakaian bahan untuk pesanan"))

        # ── 5. Buat OrderItems ───────────────────────────────────────────────
        for d in item_data_list:
            await order_repo.create_order_item(
                db,
                OrderItem(
                    order_id=order_obj.id,
                    product_id=d["product_id"],
                    product_name_snapshot=d["product_name_snapshot"],
                    unit_price_snapshot=d["unit_price_snapshot"],
                    jumlah=d["jumlah"],
                    custom_decoration_charge=d["custom_decoration_charge"],
                    subtotal=d["subtotal"],
                    hpp_snapshot=d["hpp_snapshot"],
                ),
            )

        # ── 6. Generate nomor invoice & buat Invoice ─────────────────────────
        unique_suffix = secrets.token_hex(3).upper()
        nomor_invoice = f"INV-{datetime.now().strftime('%Y%m%d')}-{order_obj.id}-{unique_suffix}"
        await order_repo.create_invoice(
            db,
            Invoice(
                order_id=order_obj.id,
                nomor_invoice=nomor_invoice,
                total_tagihan=total_harga_pesanan,
                status=InvoiceStatusEnum.unpaid,
            ),
        )
        committed_order_id = order_obj.id

        # Persist seller in-app notifications in their own savepoint so an
        # email outbox failure cannot roll them back.
        try:
            async with db.begin_nested():
                from app.services.notification_service import notify_order_created
                await notify_order_created(db, order_obj, actor_buyer=actor_buyer)
        except Exception:
            logger.exception("Order notification persistence failed for order %s", committed_order_id)

        if email_seller_on_commit:
            try:
                async with db.begin_nested():
                    customer = await db.get(Customer, customer_id)
                    if customer is not None:
                        order_obj.customer = customer
                    from app.services.email_outbox_service import enqueue_new_order_for_sellers
                    await enqueue_new_order_for_sellers(db, order_obj)
            except Exception:
                logger.exception("Order email outbox enqueue failed for order %s", committed_order_id)

        # ── 7. Commit & return dengan relasi ter-load ─────────────────────────
        await db.commit()

        # Notifikasi WhatsApp pesanan baru ke seller via Chatbot (padanan email order_created).
        # Dikirim sesudah commit karena chatbot langsung membaca detail order lewat GET /orders/{id}.
        from app.services.chatbot_notify import notify_chatbot_order_event
        try:
            await notify_chatbot_order_event(committed_order_id, "created")
        except Exception:
            logger.exception("Chatbot notification failed for committed order %s", committed_order_id)

        order = await order_repo.get_order_with_details(db, committed_order_id)
        return await _attach_payment_amounts(db, order)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"DETAIL ERROR: {repr(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Terjadi kesalahan saat memproses pesanan.") from e


async def create_custom_order(
    db: AsyncSession,
    data: CustomOrderCreate,
) -> Order:
    """
    Membuat pesanan manual seller dari produk katalog dan aksesoris stok:
    1. Upsert Customer berdasarkan customer_phone dan customer_name.
    2. Hitung harga produk dari katalog dan harga aksesoris dari biaya stok saat order.
    3. Kunci dan kurangi stok resep serta aksesoris dalam transaksi yang sama.
    4. Simpan snapshot pemakaian, harga, dan nama untuk histori/reversal.
    5. Buat Invoice otomatis dengan status 'unpaid'.
    """
    try:
        async with db.begin_nested():
            phone_norm = normalize_phone(data.customer_phone)
            customer, _ = await customer_repo.upsert(
                db,
                nomor_wa=phone_norm,
                nama=data.customer_name,
                alamat=data.customer_address,
            )
            await db.flush()

            await order_repo.lock_customer_for_order_creation(db, customer.id)

            # Cek tagihan aktif untuk customer ini
            has_active = await order_repo.check_active_unpaid_order(db, customer.id)
            if has_active:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Customer masih memiliki tagihan aktif yang belum lunas.",
                )

            # Validasi tanggal fulfillment (defense-in-depth)
            fulfillment = getattr(data, 'fulfillment_date', None)
            if fulfillment is not None:
                from app.utils.business_date import validate_fulfillment_date as _validate_date
                try:
                    _validate_date(fulfillment)
                except ValueError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(e),
                    )

            # Resolve catalog products and collect recipe requirements. Stock is
            # locked and checked below, in sorted ID order, before any write.
            total_harga_pesanan = Decimal("0.00")
            item_objects = []
            stock_item_requirements: dict[int, dict] = {}

            for item in data.items:
                product_result = await db.execute(
                    select(Product)
                    .where(Product.id == item.product_id)
                    .options(selectinload(Product.recipes).selectinload(Recipe.stock_item))
                )
                product = product_result.scalars().first()
                if not product:
                    raise HTTPException(status_code=422, detail=f"Produk ID {item.product_id} tidak ditemukan.")
                if not product.is_active or not product.is_available:
                    raise HTTPException(status_code=400, detail=f"Produk '{product.nama_produk}' tidak tersedia.")
                if product.harga_jual is None or Decimal(str(product.harga_jual)) <= 0:
                    raise HTTPException(status_code=400, detail=f"Produk '{product.nama_produk}' belum memiliki harga jual.")
                if product.minimum_order and item.qty < product.minimum_order:
                    raise HTTPException(status_code=400, detail=f"Jumlah pesanan untuk '{product.nama_produk}' minimal {product.minimum_order} pcs.")
                if not product.recipes:
                    raise HTTPException(status_code=400, detail=f"Produk '{product.nama_produk}' belum memiliki resep stok.")

                qty = item.qty
                price = Decimal(str(product.harga_jual))
                dec_charge = Decimal(str(item.custom_decoration_charge or "0.00"))
                subtotal = (price * qty) + dec_charge
                total_harga_pesanan += subtotal
                item_objects.append({
                    "product_id": product.id,
                    "product_name": product.nama_produk,
                    "unit_price": price,
                    "jumlah": qty,
                    "custom_decoration_charge": dec_charge,
                    "subtotal": subtotal,
                    "hpp_snapshot": Decimal(str(product.hpp_total or 0)),
                })

                for recipe in product.recipes:
                    stock_item = recipe.stock_item
                    if not stock_item:
                        raise HTTPException(status_code=409, detail=f"Resep produk '{product.nama_produk}' memiliki bahan yang tidak tersedia.")
                    needed = Decimal(str(recipe.jumlah_dibutuhkan)) * qty
                    if not needed.is_finite() or needed <= 0:
                        raise HTTPException(status_code=409, detail=f"Takaran resep produk '{product.nama_produk}' tidak valid.")
                    req = stock_item_requirements.setdefault(stock_item.id, {
                        "total_needed": Decimal("0"), "stock_item": stock_item,
                        "reason": "Pemakaian resep produk",
                    })
                    req["total_needed"] += needed

            # Accessory prices use the stock item's current unit cost, captured
            # once for this order; no caller-supplied price is trusted.
            accessory_objects = []
            for accessory in data.accessories:
                qty = Decimal(str(accessory.quantity))
                accessory_objects.append({
                    "stock_item_id": accessory.stock_item_id,
                    "quantity": qty,
                })
                req = stock_item_requirements.setdefault(accessory.stock_item_id, {
                    "total_needed": Decimal("0"), "stock_item": None,
                    "reason": "Pemakaian aksesoris pesanan",
                })
                if req["reason"] == "Pemakaian resep produk":
                    req["reason"] = "Pemakaian resep produk dan aksesoris pesanan"
                req["total_needed"] += qty

            # Serialize inventory deductions and prevent overselling across
            # recipe ingredients and accessories, including repeated item IDs.
            sorted_stock_ids = sorted(stock_item_requirements)
            locked_result = await db.execute(
                select(StockItem).where(StockItem.id.in_(sorted_stock_ids)).order_by(StockItem.id)
                .with_for_update().execution_options(populate_existing=True)
            ) if sorted_stock_ids else None
            locked_items = {row.id: row for row in locked_result.scalars().all()} if locked_result else {}
            for stock_id in sorted_stock_ids:
                req = stock_item_requirements[stock_id]
                stock_item = locked_items.get(stock_id)
                if not stock_item:
                    raise HTTPException(status_code=422, detail=f"Bahan stok ID {stock_id} tidak ditemukan.")
                if stock_item.stok_tersedia < req["total_needed"]:
                    raise HTTPException(status_code=400, detail=f"Stok '{stock_item.nama_item}' tidak mencukupi.")
                req["stock_item"] = stock_item

            # Price accessories from locked, current unit costs and save a
            # historical snapshot. Decimal rounding uses standard rupiah cents.
            from decimal import ROUND_HALF_UP
            for accessory_data in accessory_objects:
                stock_item = locked_items.get(accessory_data["stock_item_id"])
                if not stock_item:
                    raise HTTPException(status_code=422, detail=f"Aksesoris stok ID {accessory_data['stock_item_id']} tidak ditemukan.")
                unit_cost = Decimal(str(stock_item.harga_per_satuan))
                if not unit_cost.is_finite() or unit_cost < 0:
                    raise HTTPException(status_code=409, detail=f"Biaya stok '{stock_item.nama_item}' tidak valid.")
                subtotal = (accessory_data["quantity"] * unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                accessory_data.update({
                    "stock_item_name": stock_item.nama_item,
                    "unit": stock_item.satuan.value if hasattr(stock_item.satuan, "value") else str(stock_item.satuan),
                    "unit_cost_snapshot": unit_cost,
                    "subtotal": subtotal,
                })
                total_harga_pesanan += subtotal

            if total_harga_pesanan > MAX_ORDER_AMOUNT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Total pesanan Rp {total_harga_pesanan:,.0f} melebihi batas maksimal Rp {MAX_ORDER_AMOUNT:,.0f} per order.",
                )

            for stock_id in sorted_stock_ids:
                stock_item = locked_items[stock_id]
                stock_item.stok_tersedia -= stock_item_requirements[stock_id]["total_needed"]
                stock_item.version += 1

            # Buat Order (created_via = "seller")
            order_obj = Order(
                customer_id=customer.id,
                status=OrderStatusEnum.pending,
                metode_pengiriman=MetodePengirimanEnum(data.metode_pengiriman),
                total_harga_pesanan=total_harga_pesanan,
                created_via="seller",
                notes=data.notes,
                due_date=data.due_date,
                fulfillment_date=data.fulfillment_date if hasattr(data, 'fulfillment_date') else None,
                payment_method_preference=data.payment_method_preference,
            )
            await order_repo.create_order(db, order_obj)

            for stock_id, req in stock_item_requirements.items():
                db.add(StockMovement(
                    stock_item_id=stock_id, movement_type="ORDER",
                    quantity=-req["total_needed"],
                    unit_cost=Decimal(str(req["stock_item"].harga_per_satuan or 0)),
                    reference_type="order", reference_id=order_obj.id,
                    reason=req["reason"],
                ))

            for item_data in item_objects:
                await order_repo.create_order_item(
                    db,
                    OrderItem(
                        order_id=order_obj.id,
                        product_id=item_data["product_id"],
                        product_name_snapshot=item_data["product_name"],
                        unit_price_snapshot=item_data["unit_price"],
                        jumlah=item_data["jumlah"],
                        custom_decoration_charge=item_data["custom_decoration_charge"],
                        subtotal=item_data["subtotal"],
                        hpp_snapshot=item_data.get("hpp_snapshot", Decimal("0.00")),
                    ),
                )

            for accessory_data in accessory_objects:
                db.add(OrderAccessory(order_id=order_obj.id, **accessory_data))

            # Buat Invoice otomatis
            unique_suffix = secrets.token_hex(3).upper()
            nomor_invoice = f"INV-{datetime.now().strftime('%Y%m%d')}-{order_obj.id}-{unique_suffix}"
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
        logger.error(f"DETAIL ERROR in create_custom_order: {repr(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Gagal membuat custom order: {str(e)}",
        )


async def _attach_payment_amounts(db: AsyncSession, order: Order) -> Order:
    if not order:
        return order
    amount_paid = Decimal("0.00")
    if order.invoice:
        from app.services.payment_service import invoice_payment_totals
        amount_paid = (await invoice_payment_totals(db, order.invoice.id))["net_paid"]
            
    if order.invoice and order.invoice.total_tagihan is not None:
        try:
            amount_due = max(Decimal("0.00"), Decimal(str(order.invoice.total_tagihan)) - amount_paid)
        except Exception:
            amount_due = Decimal("0.00")
    else:
        amount_due = Decimal("0.00")
        
    order.amount_paid = amount_paid
    order.amount_due = amount_due

    # Pastikan setiap order item memiliki product_name terisi dari relasi database
    if order.order_items:
        for item in order.order_items:
            if not getattr(item, "product_name", None):
                if item.product:
                    item.product_name = getattr(item.product, "nama_produk", None) or getattr(item.product, "name", None)
                elif item.custom_product_name:
                    item.product_name = item.custom_product_name

    return order



async def get_seller_orders(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
) -> list[Order]:
    try:
        orders = await order_repo.get_all_orders(db, limit=limit, offset=offset, status=status)
        for o in orders:
            await _attach_payment_amounts(db, o)
        return orders
    except Exception as e:
        logger.error(f"Error fetching seller orders: {repr(e)}", exc_info=True)
        raise


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


async def _enqueue_order_status_email_best_effort(
    db: AsyncSession,
    order: Order,
    new_status: str,
) -> None:
    """Keep a missing or unavailable email outbox from blocking order status changes."""
    try:
        async with db.begin_nested():
            from app.services.email_outbox_service import enqueue_order_status_for_buyer
            await enqueue_order_status_for_buyer(db, order, new_status)
    except Exception:
        logger.exception("Order status email enqueue failed for order %s", order.id)


async def get_or_create_customer_for_buyer(db: AsyncSession, buyer: Buyer) -> Customer:
    """
    Retrieve existing Customer by buyer's phone (matching across all common format variants),
    or create a new Customer if none exists.
    Prevents 409 Conflict / duplicate key errors.
    """
    customer_result = await db.execute(select(Customer).where(Customer.buyer_id == buyer.id))
    customer = customer_result.scalars().first()
    if customer:
        return customer

    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if not customer:
        customer, _ = await customer_repo.upsert(
            db,
            nomor_wa=buyer.phone,
            nama=buyer.name,
            alamat=None,
        )
        await db.flush()
    if customer.buyer_id not in (None, buyer.id):
        raise HTTPException(status_code=409, detail="Data customer sudah terhubung ke akun buyer lain.")
    customer.buyer_id = buyer.id
    await db.flush()
    return customer


async def get_customer_for_buyer(db: AsyncSession, buyer: Buyer) -> Optional[Customer]:
    """Resolve a buyer's stable customer record, linking legacy phone matches once."""
    result = await db.execute(select(Customer).where(Customer.buyer_id == buyer.id))
    customer = result.scalars().first()
    if customer:
        return customer
    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if customer and customer.buyer_id in (None, buyer.id):
        customer.buyer_id = buyer.id
        await db.flush()
        return customer
    return None


async def get_buyer_orders(db: AsyncSession, buyer: Buyer) -> list[Order]:
    customer = await get_customer_for_buyer(db, buyer)
    if not customer:
        return []
    orders = await order_repo.get_orders_by_customer_id(db, customer.id)
    for o in orders:
        await _attach_payment_amounts(db, o)
    return orders


async def get_buyer_order_by_id(db: AsyncSession, buyer: Buyer, order_id: int) -> Order:
    customer = await get_customer_for_buyer(db, buyer)
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


async def create_buyer_order(
    db: AsyncSession, buyer: Buyer, data: BuyerOrderCreate,
    idempotency_key: Optional[str] = None,
) -> Order:
    try:
        buyer_result = await db.execute(
            select(Buyer).where(Buyer.id == buyer.id).with_for_update()
            .execution_options(populate_existing=True)
        )
        locked_buyer = buyer_result.scalars().first()
        if not locked_buyer:
            raise HTTPException(status_code=404, detail="Buyer tidak ditemukan")
        customer = await get_or_create_customer_for_buyer(db, locked_buyer)
        order = await create_new_order(
            db=db,
            customer_id=customer.id,
            items=[item.model_dump() for item in data.items],
            metode_pengiriman=data.metode_pengiriman,
            created_via=data.created_via,
            notes=data.notes,
            due_date=data.due_date,
            fulfillment_date=data.fulfillment_date,
            payment_method_preference=data.payment_method_preference,
            actor_buyer=locked_buyer,
            idempotency_key=idempotency_key,
            idempotency_scope=f"buyer:{locked_buyer.id}" if idempotency_key else None,
        )
        await _attach_payment_amounts(db, order)
        return order
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"DETAIL ERROR: {repr(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat memproses pesanan.",
        ) from e



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
            .execution_options(populate_existing=True)
        )
        order = result.scalars().first()
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order tidak ditemukan",
            )

        if order.status != OrderStatusEnum.pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Pesanan berstatus '{order.status.value}' tidak dapat dibatalkan kembali.",
            )
            
        if not order.invoice or order.invoice.status != InvoiceStatusEnum.unpaid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pesanan yang sudah dibayar atau dicicil tidak dapat dibatalkan secara otomatis",
            )

        pending_online = await db.scalar(select(Payment.id).where(
            Payment.invoice_id == order.invoice.id,
            Payment.payment_status == PaymentStatusEnum.pending,
            Payment.pg_order_id.is_not(None),
        ).limit(1))
        if pending_online:
            raise HTTPException(status_code=409, detail="Pembayaran online masih pending; order tidak dapat dibatalkan sebelum status gateway dipastikan")
        successful_payment = await db.scalar(select(Payment.id).where(
            Payment.invoice_id == order.invoice.id,
            Payment.payment_status.in_([PaymentStatusEnum.success, PaymentStatusEnum.refunded]),
        ).limit(1))
        if successful_payment:
            raise HTTPException(status_code=409, detail="Pembayaran sudah diterima; gunakan alur refund untuk membatalkan order")
            
        await _rollback_order_stock(db, order)

        order.status = OrderStatusEnum.cancelled
        await _enqueue_order_status_email_best_effort(
            db, order, OrderStatusEnum.cancelled.value
        )
        await db.commit()
        return {"status": "success", "message": "Pesanan berhasil dibatalkan"}

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"DETAIL ERROR: {repr(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Terjadi kesalahan saat membatalkan pesanan.",
        )


async def update_order_status(db: AsyncSession, order_id: int, new_status: str, updated_by: Optional[int] = None) -> Order:
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
        .execution_options(populate_existing=True)
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

    if new_status_enum == OrderStatusEnum.in_process:
        if not order.invoice:
            raise HTTPException(status_code=409, detail="Invoice pesanan tidak ditemukan")
        from app.services.payment_service import invoice_payment_totals
        paid = (await invoice_payment_totals(db, order.invoice.id))["net_paid"]
        minimum_to_start = Decimal(str(order.invoice.total_tagihan)) * Decimal("0.5")
        if paid < minimum_to_start:
            raise HTTPException(
                status_code=409,
                detail="Pesanan baru dapat diproses setelah pembayaran minimal DP 50% diterima.",
            )

    if new_status_enum == OrderStatusEnum.cancelled and order.invoice:
        pending_online = await db.scalar(select(Payment.id).where(
            Payment.invoice_id == order.invoice.id,
            Payment.payment_status == PaymentStatusEnum.pending,
            Payment.pg_order_id.is_not(None),
        ).limit(1))
        if pending_online:
            raise HTTPException(status_code=409, detail="Pembayaran online masih pending; order tidak dapat dibatalkan sebelum status gateway dipastikan")
        successful_payment = await db.scalar(select(Payment.id).where(
            Payment.invoice_id == order.invoice.id,
            Payment.payment_status.in_([PaymentStatusEnum.success, PaymentStatusEnum.refunded]),
        ).limit(1))
        if successful_payment:
            raise HTTPException(status_code=409, detail="Pembayaran sudah diterima; gunakan alur refund untuk membatalkan order")

    # Jika status diubah menjadi 'cancelled' atau 'refunded' dari status sebelumnya yang bukan cancelled/refunded
    if new_status in [OrderStatusEnum.cancelled.value, OrderStatusEnum.refunded.value] and order.status not in [OrderStatusEnum.cancelled, OrderStatusEnum.refunded]:
        await _rollback_order_stock(db, order)

    # A seller's explicit "refunded" transition confirms the manual payout.
    # Persist its amount before changing status; gateway refunds await signed callbacks.
    if new_status == OrderStatusEnum.refunded.value:
        from app.models.payment import RefundTransaction, RefundStatusEnum
        from app.services.payment_service import _refresh_refund_payment_state, invoice_payment_totals
        if not order.invoice:
            raise HTTPException(status_code=409, detail="Invoice refund tidak ditemukan")
        payment_result = await db.execute(
            select(Payment).where(Payment.invoice_id == order.invoice.id).with_for_update()
        )
        invoice_payments = payment_result.scalars().all()
        pending_result = await db.execute(
            select(RefundTransaction).join(Payment).where(
                Payment.invoice_id == order.invoice.id,
                RefundTransaction.status == RefundStatusEnum.pending,
            ).with_for_update()
        )
        pending_refunds = pending_result.scalars().all()
        if any(
            refund.initiated_source.endswith((":auto", ":in_progress"))
            for refund in pending_refunds
        ):
            raise HTTPException(status_code=409, detail="Refund gateway masih menunggu konfirmasi Midtrans")
        for refund in pending_refunds:
            refund.status = RefundStatusEnum.completed
            refund.completed_at = datetime.now(timezone.utc)
        if not pending_refunds:
            gross_total = sum((Decimal(str(p.jumlah_bayar)) for p in invoice_payments
                               if p.payment_status in (PaymentStatusEnum.success, PaymentStatusEnum.refunded)), Decimal("0.00"))
            existing = await invoice_payment_totals(db, order.invoice.id)
            amount_to_record = gross_total - existing["refunded"]
            if amount_to_record <= 0:
                raise HTTPException(status_code=409, detail="Tidak ada saldo refund yang dapat dikonfirmasi")
            left = amount_to_record
            for payment in invoice_payments:
                if payment.payment_status not in (PaymentStatusEnum.success, PaymentStatusEnum.refunded):
                    continue
                completed_res = await db.execute(
                    select(func.coalesce(func.sum(RefundTransaction.refund_amount), 0)).where(
                        RefundTransaction.payment_id == payment.id,
                        RefundTransaction.status == RefundStatusEnum.completed,
                    )
                )
                remaining = Decimal(str(payment.jumlah_bayar)) - Decimal(str(completed_res.scalar() or 0))
                part = min(left, remaining)
                if part > 0:
                    db.add(RefundTransaction(payment_id=payment.id, refund_amount=part,
                        status=RefundStatusEnum.completed, reason="Manual refund confirmed by seller",
                        initiated_by=updated_by, initiated_source="seller_manual_confirmation",
                        completed_at=datetime.now(timezone.utc)))
                    left -= part
                if left == 0:
                    break
        await db.flush()
        for payment in invoice_payments:
            await _refresh_refund_payment_state(db, payment)
        if order.invoice.status == InvoiceStatusEnum.refunded:
            from app.services.email_outbox_service import enqueue_order_payment_event
            totals = await invoice_payment_totals(db, order.invoice.id)
            await enqueue_order_payment_event(
                db, order.id, "refund_status_updated", f"seller-confirmed:{order.id}",
                refund_status="completed", amount=str(totals["refunded"]),
            )

    old_status_val = order.status.value
    counted_sale_statuses = {
        OrderStatusEnum.delivered,
        OrderStatusEnum.picked_up,
        OrderStatusEnum.completed,
    }
    should_count_sale = (
        order.status not in counted_sale_statuses
        and new_status_enum in counted_sale_statuses
    )
    if new_status_enum != OrderStatusEnum.refunded or order.invoice.status == InvoiceStatusEnum.refunded:
        order.status = new_status_enum
    if should_count_sale:
        quantities_by_product: dict[int, int] = {}
        for item in order.order_items:
            if item.product_id is not None:
                quantities_by_product[item.product_id] = (
                    quantities_by_product.get(item.product_id, 0) + item.jumlah
                )
        for product_id, quantity in quantities_by_product.items():
            await db.execute(
                update(Product)
                .where(Product.id == product_id)
                .values(sold_count=func.coalesce(Product.sold_count, 0) + quantity)
            )
    if new_status_enum != OrderStatusEnum.refunded or order.invoice.status == InvoiceStatusEnum.refunded:
        await _enqueue_order_status_email_best_effort(db, order, new_status_enum.value)
        from app.services.notification_service import notify_order_status_updated
        await notify_order_status_updated(db, order, old_status_val, new_status_enum.value)
    await db.commit()
    if should_count_sale:
        from app.core.cache import app_cache
        app_cache.invalidate_prefix("products:")

    # Re-query order with details to avoid lazy loading issues
    order_refetched = await order_repo.get_order_with_details(db, order_id)
    await _attach_payment_amounts(db, order_refetched)
    actual_status = order_refetched.status.value if hasattr(order_refetched.status, "value") else str(order_refetched.status)

    if actual_status == "ready":
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "ready")
    elif actual_status == OrderStatusEnum.in_process.value:
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "processing")
    elif actual_status == OrderStatusEnum.completed.value:
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "completed")
    elif actual_status == OrderStatusEnum.delivered.value:
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "delivered")
    elif actual_status == OrderStatusEnum.picked_up.value:
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "picked_up")
    elif actual_status == OrderStatusEnum.cancelled.value:
        from app.services.chatbot_notify import notify_chatbot_order_event
        await notify_chatbot_order_event(order_id, "cancelled")
    elif actual_status == OrderStatusEnum.refunded.value:
        # Sinyal Manual Refund Completed (Poin 2.b):
        # Dipicu saat Admin/Seller mengubah status pesanan menjadi refunded di Dashboard Site
        from app.services.chatbot_notify import notify_refund_status
        await notify_refund_status(order_id)
            
    return order_refetched


async def _rollback_order_stock(db: AsyncSession, order: Order) -> None:
    from app.models.stock_item import StockItem, StockMovement

    # Serialize every reversal path and make repeated calls idempotent.
    await db.execute(select(Order.id).where(Order.id == order.id).with_for_update())
    already_reversed = await db.execute(
        select(StockMovement.id).where(
            StockMovement.reference_type == "order",
            StockMovement.reference_id == order.id,
            StockMovement.movement_type == "REVERSAL",
        ).limit(1)
    )
    if already_reversed.scalar_one_or_none() is not None:
        return

    # ORDER movements are the immutable record of the recipe quantities actually
    # consumed at creation time. Current recipe rows may have changed since then.
    consumed = await db.execute(
        select(StockMovement.stock_item_id, func.sum(StockMovement.quantity))
        .where(
            StockMovement.reference_type == "order",
            StockMovement.reference_id == order.id,
            StockMovement.movement_type == "ORDER",
        )
        .group_by(StockMovement.stock_item_id)
    )
    stock_item_returns = {
        stock_id: -Decimal(str(quantity))
        for stock_id, quantity in consumed.all()
        if quantity is not None and Decimal(str(quantity)) < 0
    }
    if not stock_item_returns:
        logger.warning(
            "[INVENTORY_AUDIT] order_has_no_consumption_ledger | order_id=%s | "
            "created_at=%s | action=stock_not_adjusted",
            order.id, order.created_at,
        )

    max_retries = 3
    for stock_id, qty_return in stock_item_returns.items():
        for attempt in range(1, max_retries + 1):
            stock_result = await db.execute(select(StockItem).where(StockItem.id == stock_id))
            stock_item = stock_result.scalars().first()
            if not stock_item:
                raise HTTPException(status_code=409, detail=f"Bahan baku {stock_id} untuk reversal tidak ditemukan")

            result = await db.execute(
                update(StockItem)
                .where(StockItem.id == stock_id, StockItem.version == stock_item.version)
                .values(stok_tersedia=StockItem.stok_tersedia + qty_return,
                        version=StockItem.version + 1)
            )
            if result.rowcount == 1:
                db.add(StockMovement(
                    stock_item_id=stock_id, movement_type="REVERSAL", quantity=qty_return,
                    unit_cost=Decimal(str(stock_item.harga_per_satuan or 0)),
                    reference_type="order", reference_id=order.id,
                    reason="Pemulihan bahan berdasarkan pemakaian saat order dibuat",
                ))
                break
            if attempt == max_retries:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Terjadi kegagalan pemulihan stok karena transaksi bersamaan. Silakan coba lagi.",
                )


async def cancel_and_refund_order(
    db: AsyncSession,
    order_id: int,
    reason: str,
    is_service: bool = False,
    customer_phone: Optional[str] = None,
    refund_amount: Optional[Decimal] = None,
    initiated_by: Optional[int] = None,
):
    from app.services.payment_service import process_refund
    from app.utils.phone import normalize_phone
    from app.schemas.order import RefundResponse
    
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
        .execution_options(populate_existing=True)
    )
    order = result.scalars().first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )

    # ── VALIDASI KHUSUS SERVICE CHATBOT ─────────────────────────────────────
    if is_service:
        # a. Verification Ownership
        if not customer_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Field 'nomor_wa' wajib disertakan untuk refund via Chatbot.",
            )
        req_phone = normalize_phone(customer_phone)
        order_phone = normalize_phone(order.customer.nomor_wa) if order.customer and order.customer.nomor_wa else ""
        if req_phone != order_phone:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nomor WhatsApp tidak cocok dengan data pemesan.",
            )

        # b. Strict Status Check: HANYA diizinkan jika status pesanan masih 'pending'
        if order.status != OrderStatusEnum.pending:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Refund melalui chatbot hanya diizinkan saat status pesanan masih 'pending'. Status saat ini: '{order.status.value}'.",
            )

    # ── VALIDASI STATUS TERMINAL & INVOICE (Berlaku untuk Admin & Chatbot) ──
    from app.core.state_machine import is_order_terminal
    if order.status == OrderStatusEnum.refunded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pesanan sudah dalam status 'refunded'."
        )
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

    pending_online = await db.scalar(select(Payment.id).where(
        Payment.invoice_id == order.invoice.id,
        Payment.payment_status == PaymentStatusEnum.pending,
        Payment.pg_order_id.is_not(None),
    ).limit(1))
    if pending_online:
        raise HTTPException(status_code=409, detail="Pembayaran online masih pending; pastikan status gateway sebelum refund atau pembatalan")

    # Panggil payment_service untuk eksekusi API Midtrans, commit DB, dan kirim webhook post-commit (jika auto)
    refund_mode = await process_refund(
        db, order_id, reason, amount=refund_amount, initiated_by=initiated_by,
        initiated_source="chatbot" if is_service else "seller",
    )

    # Refund request is not proof that money was returned. Cancel/release the order
    # while the refund remains pending; signed gateway callback or seller confirmation
    # records the completed amount and may transition it to fully refunded.
    if order.status != OrderStatusEnum.cancelled:
        await _rollback_order_stock(db, order)
        order.status = OrderStatusEnum.cancelled
        from app.services.email_outbox_service import enqueue_order_status_for_buyer
        await enqueue_order_status_for_buyer(db, order, OrderStatusEnum.cancelled.value)
    await db.commit()

    return RefundResponse(
        message="Order refund processed successfully",
        order_id=order.id,
        status=order.status.value if hasattr(order.status, 'value') else str(order.status),
        payment_status="refund_pending",
        refund_mode=refund_mode,
    )


async def get_order_stats(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Mengambil data ringkasan statistik pesanan dengan penanganan tanggal yang tangguh.
    """
    from datetime import time
    start_dt = None
    end_dt = None
    if start_date and str(start_date).strip():
        try:
            start_dt = datetime.combine(datetime.strptime(str(start_date).strip(), "%Y-%m-%d"), time.min)
        except ValueError:
            pass
    if end_date and str(end_date).strip():
        try:
            end_dt = datetime.combine(datetime.strptime(str(end_date).strip(), "%Y-%m-%d"), time.max)
        except ValueError:
            pass

    return await order_repo.get_order_stats(db, start_dt, end_dt)


async def update_order_fulfillment_date(
    db: AsyncSession,
    order_id: int,
    new_fulfillment_date: datetime,
) -> Order:
    """
    Update tanggal fulfillment/pickup pesanan dengan memvalidasi aturan H+1 s/d H+30.
    Menolak pembaruan jika pesanan sudah dalam status terminal atau selesai/batal.
    """
    from app.utils.business_date import validate_fulfillment_date as _validate_date
    from app.core.state_machine import is_order_terminal

    # 1. Validasi aturan bisnis tanggal H+1 s/d H+30
    try:
        _validate_date(new_fulfillment_date)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    order = await order_repo.get_order_with_details(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )

    # 2. Validasi status order: tidak boleh diubah jika terminal / selesai / dibatalkan
    if is_order_terminal(order.status) or order.status in [
        OrderStatusEnum.completed,
        OrderStatusEnum.cancelled,
        OrderStatusEnum.refunded,
        OrderStatusEnum.picked_up,
        OrderStatusEnum.delivered,
    ]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tanggal pesanan tidak dapat diubah karena pesanan sudah berstatus '{order.status.value}'.",
        )

    order.fulfillment_date = new_fulfillment_date
    await db.commit()

    order_refetched = await order_repo.get_order_with_details(db, order_id)
    return await _attach_payment_amounts(db, order_refetched)
