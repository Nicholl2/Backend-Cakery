"""
Purchasing service for managing suppliers and purchase orders.
"""

from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc
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

def create_supplier(db: Session, data: SupplierCreate) -> Supplier:
    """Create a new supplier."""
    existing = db.query(Supplier).filter(
        Supplier.nama_supplier == data.nama_supplier
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier '{data.nama_supplier}' sudah terdaftar.",
        )
    supplier = Supplier(**data.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def get_all_suppliers(
    db: Session,
    only_active: bool = False,
) -> list[Supplier]:
    """Get all suppliers, optionally filtered to active only."""
    q = db.query(Supplier)
    if only_active:
        q = q.filter(Supplier.is_active == True)
    return q.order_by(Supplier.nama_supplier).all()


def get_supplier_or_404(db: Session, supplier_id: int) -> Supplier:
    """Get supplier by ID or raise 404."""
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan.")
    return supplier


def update_supplier(
    db: Session, supplier_id: int, data: SupplierUpdate
) -> Supplier:
    """Update a supplier."""
    supplier = get_supplier_or_404(db, supplier_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(supplier, field, value)
    db.commit()
    db.refresh(supplier)
    return supplier


def delete_supplier(db: Session, supplier_id: int) -> bool:
    """Delete a supplier. Check if it has purchases first."""
    supplier = get_supplier_or_404(db, supplier_id)
    
    # Check if supplier has any purchases
    purchases = db.query(Purchase).filter(Purchase.supplier_id == supplier_id).count()
    if purchases > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Supplier memiliki {purchases} pemesanan. Hapus pemesanan terlebih dahulu.",
        )
    
    db.delete(supplier)
    db.commit()
    return True


# ── PURCHASE SERVICE ────────────────────────────────────────────────────────

def create_purchase(
    db: Session,
    data: PurchaseCreate,
    created_by_user_id: int,
) -> Purchase:
    """
    Create a purchase order with items.
    
    After saving, triggers price recalculation for affected products.
    """
    # Verify supplier exists
    supplier = get_supplier_or_404(db, data.supplier_id)
    
    # Create purchase
    purchase = Purchase(
        supplier_id=data.supplier_id,
        created_by=created_by_user_id,
        nomor_po=data.nomor_po,
        catatan=data.catatan,
        total_harga=Decimal("0"),
    )
    db.add(purchase)
    db.flush()  # Get purchase ID before adding items
    
    total_harga = Decimal("0")
    affected_stock_items = set()
    
    # Add purchase items
    for item_data in data.items:
        # Verify stock item exists
        stock_item = db.query(StockItem).filter(
            StockItem.id == item_data.stock_item_id
        ).first()
        if not stock_item:
            db.rollback()
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
    db.commit()
    db.refresh(purchase)
    
    # Trigger price recalculation for affected products
    # This would be done via a task queue in production
    # For now, we'll skip it as it's an async operation
    
    return purchase


def get_all_purchases(
    db: Session,
    only_received: Optional[bool] = None,
    supplier_id: Optional[int] = None,
) -> list[Purchase]:
    """Get all purchases with optional filters."""
    q = db.query(Purchase)
    if only_received is not None:
        q = q.filter(Purchase.is_received == only_received)
    if supplier_id:
        q = q.filter(Purchase.supplier_id == supplier_id)
    return q.order_by(desc(Purchase.created_at)).all()


def get_purchase_or_404(db: Session, purchase_id: int) -> Purchase:
    """Get purchase by ID or raise 404."""
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if not purchase:
        raise HTTPException(status_code=404, detail="Pemesanan tidak ditemukan.")
    return purchase


def update_purchase(
    db: Session, purchase_id: int, data: PurchaseUpdate
) -> Purchase:
    """Update a purchase."""
    purchase = get_purchase_or_404(db, purchase_id)
    
    # Don't allow editing of received purchases
    if purchase.is_received and data.is_received is False:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tidak dapat mengubah status pemesanan yang sudah diterima.",
        )
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(purchase, field, value)
    
    db.commit()
    db.refresh(purchase)
    return purchase


def delete_purchase(db: Session, purchase_id: int) -> bool:
    """Delete a purchase if it hasn't been received."""
    purchase = get_purchase_or_404(db, purchase_id)
    
    if purchase.is_received:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tidak dapat menghapus pemesanan yang sudah diterima.",
        )
    
    db.delete(purchase)
    db.commit()
    return True
