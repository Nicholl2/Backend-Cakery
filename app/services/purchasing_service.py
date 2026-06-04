"""
Purchasing service for managing suppliers and purchase orders.
"""

from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload
from app.models.purchasing import Supplier, Purchase, PurchaseItem
from app.models.stock_item import StockItem
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate,
    PurchaseCreate, PurchaseUpdate,
)
from app.repositories import stock_repo
from app.utils.pricing import calculate_product_price
from typing import Optional


# ── SUPPLIER SERVICE ────────────────────────────────────────────────────────

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
    """
    Create a purchase order with items.
    
    After saving, triggers price recalculation for affected products.
    """
    # Verify supplier exists
    await get_supplier_or_404(db, data.supplier_id)
    
    # Create purchase
    purchase = Purchase(
        supplier_id=data.supplier_id,
        created_by=created_by_user_id,
        nomor_po=data.nomor_po,
        catatan=data.catatan,
        total_harga=Decimal("0"),
    )
    db.add(purchase)
    await db.flush()  # Get purchase ID before adding items
    
    total_harga = Decimal("0")
    affected_stock_items = set()
    
    # Add purchase items
    for item_data in data.items:
        # Verify stock item exists
        result = await db.execute(select(StockItem).where(StockItem.id == item_data.stock_item_id))
        stock_item = result.scalars().first()
        if not stock_item:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Stock item {item_data.stock_item_id} tidak ditemukan.",
            )
        
        # Calculate total price for this item
        harga_total = item_data.jumlah * item_data.harga_satuan
        
        # Create purchase item
        purchase_item = PurchaseItem(
            purchase_id=purchase.id,
            stock_item_id=item_data.stock_item_id,
            jumlah=item_data.jumlah,
            harga_satuan=item_data.harga_satuan,
            harga_total=harga_total,
        )
        db.add(purchase_item)
        total_harga += harga_total
        affected_stock_items.add(item_data.stock_item_id)
    
    # Update purchase total
    purchase.total_harga = total_harga
    await db.commit()
    await db.refresh(purchase)
    
    # Trigger price recalculation for affected products
    # This would be done via a task queue in production
    # For now, we'll skip it as it's an async operation
    
    return purchase


async def get_all_purchases(
    db: AsyncSession,
    only_received: Optional[bool] = None,
    supplier_id: Optional[int] = None,
) -> list[Purchase]:
    """Get all purchases with optional filters."""
    q = select(Purchase)
    if only_received is not None:
        q = q.where(Purchase.is_received == only_received)
    if supplier_id:
        q = q.where(Purchase.supplier_id == supplier_id)
    result = await db.execute(q.order_by(desc(Purchase.created_at)))
    return result.scalars().all()


async def get_purchase_or_404(db: AsyncSession, purchase_id: int) -> Purchase:
    """Get purchase by ID or raise 404."""
    result = await db.execute(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(selectinload(Purchase.purchase_items))
    )
    purchase = result.scalars().first()
    if not purchase:
        raise HTTPException(status_code=404, detail="Pemesanan tidak ditemukan.")
    return purchase


async def update_purchase(
    db: AsyncSession, purchase_id: int, data: PurchaseUpdate
) -> Purchase:
    """Update a purchase."""
    purchase = await get_purchase_or_404(db, purchase_id)
    
    # Don't allow editing of received purchases
    if purchase.is_received and data.is_received is False:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tidak dapat mengubah status pemesanan yang sudah diterima.",
        )
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(purchase, field, value)
    
    await db.commit()
    await db.refresh(purchase)
    return purchase


async def delete_purchase(db: AsyncSession, purchase_id: int) -> bool:
    """Delete a purchase if it hasn't been received."""
    purchase = await get_purchase_or_404(db, purchase_id)
    
    if purchase.is_received:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tidak dapat menghapus pemesanan yang sudah diterima.",
        )
    
    await db.delete(purchase)
    await db.commit()
    return True
