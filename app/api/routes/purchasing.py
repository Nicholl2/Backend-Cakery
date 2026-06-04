"""
Purchasing routes for supplier and purchase management.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate, SupplierOut,
    PurchaseCreate, PurchaseUpdate, PurchaseOut, PurchaseDetailOut,
)
from app.services import purchasing_service

router = APIRouter(prefix="/purchasing", tags=["Purchasing"])


# ── SUPPLIER ROUTES ──────────────────────────────────────────────────────────

@router.post("/suppliers", response_model=SupplierOut, status_code=201,
             summary="Tambah supplier baru")
def create_supplier(data: SupplierCreate, db: Session = Depends(get_db)):
    return purchasing_service.create_supplier(db, data)


@router.get("/suppliers", response_model=list[SupplierOut],
            summary="List semua supplier — bisa filter hanya yang aktif")
def list_suppliers(
    only_active: bool = Query(False, description="True = hanya supplier aktif"),
    db: Session = Depends(get_db),
):
    return purchasing_service.get_all_suppliers(db, only_active)


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut,
            summary="Lihat detail supplier")
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    return purchasing_service.get_supplier_or_404(db, supplier_id)


@router.put("/suppliers/{supplier_id}", response_model=SupplierOut,
            summary="Edit data supplier")
def update_supplier(
    supplier_id: int, data: SupplierUpdate, db: Session = Depends(get_db)
):
    return purchasing_service.update_supplier(db, supplier_id, data)


@router.delete("/suppliers/{supplier_id}",
               summary="Hapus supplier — gagal jika ada pemesanan terkait")
def delete_supplier(supplier_id: int, db: Session = Depends(get_db)):
    purchasing_service.delete_supplier(db, supplier_id)
    return {"deleted": True, "supplier_id": supplier_id}


# ── PURCHASE ROUTES ──────────────────────────────────────────────────────────

@router.post("/purchases", response_model=PurchaseOut, status_code=201,
             summary="Buat pemesanan baru dengan item-item")
def create_purchase(
    data: PurchaseCreate,
    db: Session = Depends(get_db),
):
    # In production, this would be from JWT token
    created_by_user_id = 1  # Placeholder
    return purchasing_service.create_purchase(db, data, created_by_user_id)


@router.get("/purchases", response_model=list[PurchaseOut],
            summary="List semua pemesanan — bisa filter by status / supplier")
def list_purchases(
    only_received: Optional[bool] = Query(None, description="True = sudah diterima, False = belum diterima"),
    supplier_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return purchasing_service.get_all_purchases(db, only_received, supplier_id)


@router.get("/purchases/{purchase_id}", response_model=PurchaseDetailOut,
            summary="Lihat detail pemesanan dengan semua item")
def get_purchase(purchase_id: int, db: Session = Depends(get_db)):
    return purchasing_service.get_purchase_or_404(db, purchase_id)


@router.put("/purchases/{purchase_id}", response_model=PurchaseOut,
            summary="Update pemesanan (status diterima, tanggal diterima, catatan)")
def update_purchase(
    purchase_id: int, data: PurchaseUpdate, db: Session = Depends(get_db)
):
    return purchasing_service.update_purchase(db, purchase_id, data)


@router.delete("/purchases/{purchase_id}",
               summary="Hapus pemesanan — gagal jika sudah diterima")
def delete_purchase(purchase_id: int, db: Session = Depends(get_db)):
    purchasing_service.delete_purchase(db, purchase_id)
    return {"deleted": True, "purchase_id": purchase_id}
