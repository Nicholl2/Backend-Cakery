from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload
from app.models.purchasing import Supplier, Purchase, PurchaseItem
from app.models.stock_item import StockItem
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate,
    PurchaseCreate, PurchaseItemCreate, PurchaseUpdate,
)
from app.repositories import stock_repo, product_repo
from app.models.recipe import Recipe
from typing import Optional

MAX_PURCHASE_TOTAL = Decimal("9999999999999.99")
MAX_UNIT_COST = Decimal("999999.9999")

async def create_supplier(db: AsyncSession, data: SupplierCreate) -> Supplier:
    """Create a new supplier."""
    result = await db.execute(select(Supplier).where(Supplier.nama_supplier == data.nama_supplier))
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier '{data.nama_supplier}' sudah terdaftar.",
        )
    supplier = Supplier(**data.model_dump())
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def get_all_suppliers(
    db: AsyncSession,
    only_active: bool = False,
) -> list[Supplier]:
    """Get all suppliers, optionally filtered to active only."""
    q = select(Supplier)
    if only_active:
        q = q.where(Supplier.is_active == True)
    result = await db.execute(q.order_by(Supplier.nama_supplier))
    return result.scalars().all()


async def get_supplier_or_404(db: AsyncSession, supplier_id: int) -> Supplier:
    """Get supplier by ID or raise 404."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalars().first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan.")
    return supplier


async def update_supplier(
    db: AsyncSession, supplier_id: int, data: SupplierUpdate
) -> Supplier:
    """Update a supplier."""
    supplier = await get_supplier_or_404(db, supplier_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(supplier, field, value)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def delete_supplier(db: AsyncSession, supplier_id: int) -> bool:
    """Delete a supplier. Check if it has purchases first."""
    supplier = await get_supplier_or_404(db, supplier_id)
    
    # Check if supplier has any purchases
    result = await db.execute(
        select(func.count()).select_from(Purchase).where(Purchase.supplier_id == supplier_id)
    )
    purchases = result.scalar_one()
    if purchases > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier memiliki {purchases} pemesanan. Hapus pemesanan terlebih dahulu.",
        )
    
    await db.delete(supplier)
    await db.commit()
    return True


# ── PURCHASE SERVICE ────────────────────────────────────────────────────────

async def create_purchase(
    db: AsyncSession,
    data: PurchaseCreate,
    created_by_user_id: int,
) -> Purchase:
    supplier = await get_supplier_or_404(db, data.supplier_id)
    if not supplier.is_active:
        raise HTTPException(422, "Supplier tidak aktif.")
    normalized_items = await _normalize_purchase_items(db, data.items)
    total_harga = sum((harga_total for _, _, harga_total in normalized_items), Decimal("0"))
    _validate_purchase_total(total_harga)

    purchase = Purchase(
        supplier_id=data.supplier_id,
        created_by=created_by_user_id,
        nomor_po=data.nomor_po,
        catatan=data.catatan,
        total_harga=Decimal("0"),
    )
    db.add(purchase)
    await db.flush()

    for item_data, harga_satuan, harga_total in normalized_items:
        db.add(PurchaseItem(
            purchase_id=purchase.id,
            stock_item_id=item_data.stock_item_id,
            jumlah=item_data.jumlah,
            harga_satuan=harga_satuan,
            harga_total=harga_total,
            cost_deviation_confirmed=item_data.confirm_cost_deviation,
        ))
    purchase.total_harga = total_harga
    await db.commit()
    await db.refresh(purchase)

    return purchase


async def _normalize_purchase_items(db: AsyncSession, items):
    """Validate item references and normalize base-unit purchase prices."""
    normalized = []
    for item_data in items:
        result = await db.execute(select(StockItem).where(StockItem.id == item_data.stock_item_id))
        stock_item = result.scalars().first()
        if not stock_item:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Stock item {item_data.stock_item_id} tidak ditemukan.",
            )

        harga_total = (
            item_data.harga_total
            if item_data.harga_total is not None
            else (item_data.jumlah * item_data.harga_satuan).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        harga_satuan = (harga_total / item_data.jumlah).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if harga_satuan <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Harga total terlalu kecil untuk dihitung per satuan inventory.",
            )
        if harga_satuan > MAX_UNIT_COST or harga_total > MAX_PURCHASE_TOTAL:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Harga per satuan atau total item melewati batas yang didukung.",
            )
        current_average = Decimal(str(stock_item.harga_per_satuan or "0"))
        if current_average > 0 and not item_data.confirm_cost_deviation:
            ratio = max(harga_satuan, current_average) / min(harga_satuan, current_average)
            if ratio >= Decimal("10"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Harga pembelian menghasilkan {harga_satuan} per {stock_item.satuan.value}, "
                        f"sedangkan average cost saat ini {current_average} per {stock_item.satuan.value}. "
                        "Periksa jumlah dan total pembelian; jika benar, kirim ulang dengan "
                        "confirm_cost_deviation=true."
                    ),
                )
        normalized.append((item_data, harga_satuan, harga_total))
    return normalized


def _validate_purchase_total(total: Decimal) -> None:
    if total > MAX_PURCHASE_TOTAL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Total purchase melewati batas yang didukung.",
        )


async def get_all_purchases(
    db: AsyncSession,
    only_received: Optional[bool] = None,
    supplier_id: Optional[int] = None,
    only_cancelled: Optional[bool] = None,
) -> list[Purchase]:
    q = select(Purchase).options(
        selectinload(Purchase.supplier),
        selectinload(Purchase.created_by_user),
        selectinload(Purchase.purchase_items).selectinload(PurchaseItem.stock_item),
    )
    if only_received is not None:
        q = q.where(Purchase.is_received == only_received)
        if only_received is False and only_cancelled is None:
            q = q.where(Purchase.is_cancelled.is_(False))
    if supplier_id:
        q = q.where(Purchase.supplier_id == supplier_id)
    if only_cancelled is not None:
        q = q.where(Purchase.is_cancelled == only_cancelled)
    result = await db.execute(q.order_by(desc(Purchase.created_at)))
    return result.scalars().all()


async def get_purchase_or_404(db: AsyncSession, purchase_id: int) -> Purchase:
    result = await db.execute(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(
            selectinload(Purchase.supplier),
            selectinload(Purchase.created_by_user),
            selectinload(Purchase.purchase_items).selectinload(PurchaseItem.stock_item),
        )
    )
    purchase = result.scalars().first()
    if not purchase:
        raise HTTPException(404, "Pemesanan tidak ditemukan.")
    return purchase


async def update_purchase(
    db: AsyncSession, purchase_id: int, data: PurchaseUpdate
) -> Purchase:
    result = await db.execute(select(Purchase).where(Purchase.id == purchase_id).with_for_update().options(
        selectinload(Purchase.purchase_items), selectinload(Purchase.supplier),
        selectinload(Purchase.created_by_user)))
    purchase = result.scalars().first()
    if not purchase:
        raise HTTPException(404, "Pemesanan tidak ditemukan.")

    if purchase.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Purchase yang sudah dibatalkan tidak dapat diubah atau diterima.",
        )

    changes = data.model_dump(exclude_unset=True)
    if purchase.is_received and any(
        field in changes for field in ("supplier_id", "nomor_po", "items", "is_received")
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Purchase yang sudah diterima tidak dapat mengubah supplier, nomor PO, item, atau status.",
        )

    if "supplier_id" in changes:
        supplier = await get_supplier_or_404(db, changes["supplier_id"])
        if not supplier.is_active:
            raise HTTPException(422, "Supplier tidak aktif.")

    if "items" in changes:
        item_payloads = [PurchaseItemCreate.model_validate(item) for item in changes.pop("items")]
        normalized_items = await _normalize_purchase_items(db, item_payloads)
        purchase_total = sum(
            (harga_total for _, _, harga_total in normalized_items), Decimal("0")
        )
        _validate_purchase_total(purchase_total)
        purchase.purchase_items.clear()
        await db.flush()
        purchase.purchase_items.extend([
            PurchaseItem(
                stock_item_id=item_data.stock_item_id,
                jumlah=item_data.jumlah,
                harga_satuan=harga_satuan,
                harga_total=harga_total,
                cost_deviation_confirmed=item_data.confirm_cost_deviation,
            )
            for item_data, harga_satuan, harga_total in normalized_items
        ])
        purchase.total_harga = purchase_total

    # Deteksi transisi menjadi received
    is_becoming_received = (not purchase.is_received) and (data.is_received is True)

    for field, value in changes.items():
        setattr(purchase, field, value)

    if is_becoming_received:
        if not purchase.purchase_items:
            raise HTTPException(status_code=422, detail="Purchase tanpa item tidak dapat diterima.")
        if not purchase.tanggal_diterima:
            purchase.tanggal_diterima = datetime.now(timezone.utc)

        affected_stock_ids: set[int] = set()
        for item in purchase.purchase_items:
            updated_item = await stock_repo.update_average_cost(
                db=db,
                stock_id=item.stock_item_id,
                qty_masuk=item.jumlah,
                harga_beli_total=item.harga_total,
                reference_type="purchase",
                reference_id=purchase.id,
                created_by=purchase.created_by,
                commit=False,
            )
            if not updated_item:
                raise HTTPException(status_code=422, detail=f"Stock item {item.stock_item_id} tidak ditemukan.")
            affected_stock_ids.add(item.stock_item_id)

        # Recalculate HPP produk resep yang menggunakan bahan baku tersebut
        for stock_id in affected_stock_ids:
            rows = await db.execute(
                select(Recipe.product_id)
                .where(Recipe.stock_item_id == stock_id)
                .distinct()
            )
            for (pid,) in rows.all():
                await product_repo.calculate_and_update_product_price(db, pid, commit=False)

    await db.commit()
    await db.refresh(purchase)
    return purchase


async def cancel_purchase(
    db: AsyncSession, purchase_id: int, reason: str, cancelled_by: int | None = None
) -> Purchase:
    result = await db.execute(
        select(Purchase).where(Purchase.id == purchase_id).with_for_update()
    )
    purchase = result.scalars().first()
    if not purchase:
        raise HTTPException(404, "Pemesanan tidak ditemukan.")
    if purchase.is_received:
        raise HTTPException(409, "Purchase yang sudah diterima tidak dapat dibatalkan.")
    if purchase.is_cancelled:
        raise HTTPException(409, "Purchase sudah dibatalkan.")

    reason = reason.strip()
    if len(reason) < 3 or len(reason) > 500:
        raise HTTPException(422, "Alasan pembatalan harus berisi 3–500 karakter.")

    purchase.is_cancelled = True
    purchase.cancelled_at = datetime.now(timezone.utc)
    purchase.cancellation_reason = reason.strip()
    purchase.cancelled_by = cancelled_by
    await db.commit()
    await db.refresh(purchase)
    return purchase


async def delete_purchase(
    db: AsyncSession, purchase_id: int, cancelled_by: int | None = None
) -> bool:
    """Backward-compatible DELETE route; preserve a cancellation audit record."""
    await cancel_purchase(db, purchase_id, "Dibatalkan oleh pengguna.", cancelled_by)
    return True
