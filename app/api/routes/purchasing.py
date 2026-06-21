"""
Purchasing routes for supplier and purchase management.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.api.dependencies import get_current_user_id
from app.schemas.purchasing import (
    SupplierCreate, SupplierUpdate, SupplierOut,
    PurchaseCreate, PurchaseUpdate, PurchaseOut, PurchaseDetailOut,
)
from app.services import purchasing_service

router = APIRouter(tags=["Purchasing"])


# ── SUPPLIER ROUTES ──────────────────────────────────────────────────────────

@router.post("/suppliers", response_model=SupplierOut, status_code=201,
             summary="Tambah supplier baru")
async def create_supplier(data: SupplierCreate, db: AsyncSession = Depends(get_db)):
    return await purchasing_service.create_supplier(db, data)


@router.get("/suppliers", response_model=list[SupplierOut],
            summary="List semua supplier — bisa filter hanya yang aktif")
async def list_suppliers(
    only_active: bool = Query(False, description="True = hanya supplier aktif"),
    db: AsyncSession = Depends(get_db),
):
    return await purchasing_service.get_all_suppliers(db, only_active)


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut,
            summary="Lihat detail supplier")
async def get_supplier(supplier_id: int, db: AsyncSession = Depends(get_db)):
    return await purchasing_service.get_supplier_or_404(db, supplier_id)


@router.put("/suppliers/{supplier_id}", response_model=SupplierOut,
            summary="Edit data supplier")
async def update_supplier(
    supplier_id: int, data: SupplierUpdate, db: AsyncSession = Depends(get_db)
):
    return await purchasing_service.update_supplier(db, supplier_id, data)


@router.delete("/suppliers/{supplier_id}",
               summary="Hapus supplier — gagal jika ada pemesanan terkait")
async def delete_supplier(supplier_id: int, db: AsyncSession = Depends(get_db)):
    await purchasing_service.delete_supplier(db, supplier_id)
    return {"deleted": True, "supplier_id": supplier_id}


# ── PURCHASE ROUTES ──────────────────────────────────────────────────────────

@router.post("/purchases", response_model=PurchaseOut, status_code=201,
             summary="Buat pemesanan baru dengan item-item")
async def create_purchase(
    data: PurchaseCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await purchasing_service.create_purchase(db, data, user_id)


@router.get("/purchases", response_model=list[PurchaseOut],
            summary="List semua pemesanan — bisa filter by status / supplier")
async def list_purchases(
    only_received: Optional[bool] = Query(None, description="True = sudah diterima, False = belum diterima"),
    supplier_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await purchasing_service.get_all_purchases(db, only_received, supplier_id)


@router.get("/purchases/{purchase_id}", response_model=PurchaseDetailOut,
            summary="Lihat detail pemesanan dengan semua item")
async def get_purchase(purchase_id: int, db: AsyncSession = Depends(get_db)):
    return await purchasing_service.get_purchase_or_404(db, purchase_id)


@router.put("/purchases/{purchase_id}", response_model=PurchaseOut,
            summary="Update pemesanan (status diterima, tanggal diterima, catatan)")
async def update_purchase(
    purchase_id: int, data: PurchaseUpdate, db: AsyncSession = Depends(get_db)
):
    return await purchasing_service.update_purchase(db, purchase_id, data)


@router.delete("/purchases/{purchase_id}",
               summary="Hapus pemesanan — gagal jika sudah diterima")
async def delete_purchase(purchase_id: int, db: AsyncSession = Depends(get_db)):
    await purchasing_service.delete_purchase(db, purchase_id)
    return {"deleted": True, "purchase_id": purchase_id}
