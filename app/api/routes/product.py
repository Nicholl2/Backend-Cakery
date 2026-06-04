from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    SetPriceRequest, SetPriceResponse,
    PricingResponse, PriceHistoryOut,
)
from app.services import product_service

router = APIRouter(prefix="/products", tags=["Products"])


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("/", response_model=ProductOut, status_code=201,
             summary="Buat produk baru — hpp_total dimulai dari 0, isi resep dulu")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    return product_service.create_product(db, data)


@router.get("/", response_model=list[ProductOut],
            summary="List produk — filter by is_active / kategori")
def list_products(
    only_active: bool = Query(False, description="True = hanya produk aktif (untuk Buyer Site)"),
    kategori: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    return product_service.get_all_products(db, only_active, kategori)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = product_service.get_product_or_404(db, product_id)
    return ProductOut.model_validate(p)


@router.put("/{product_id}", response_model=ProductOut,
            summary="Edit produk (Admin/Owner) — nama, deskripsi, kategori, is_active")
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db)):
    return product_service.update_product(db, product_id, data)


@router.delete("/{product_id}",
               summary="Hapus produk beserta semua resep dan riwayat harganya")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    return product_service.delete_product(db, product_id)


# ── Pricing — khusus Owner ────────────────────────────────────────────────────

@router.patch("/{product_id}/price", response_model=SetPriceResponse,
              summary="Owner menetapkan harga jual — Use Case 2 (Set Product Prices). "
                      "Sistem beri warning jika harga < HPP. Riwayat perubahan dicatat otomatis.")
def set_price(product_id: int, data: SetPriceRequest, db: Session = Depends(get_db)):
    return product_service.set_product_price(db, product_id, data)


@router.get("/{product_id}/pricing", response_model=PricingResponse,
            summary="Lihat HPP breakdown per bahan + margin vs harga jual saat ini")
def get_pricing(product_id: int, db: Session = Depends(get_db)):
    return product_service.get_pricing_breakdown(db, product_id)


@router.get("/{product_id}/price-history", response_model=list[PriceHistoryOut],
            summary="Riwayat perubahan harga jual oleh Owner — Use Case 6 (View Price History)")
def get_price_history(product_id: int, db: Session = Depends(get_db)):
    return product_service.get_price_history(db, product_id)