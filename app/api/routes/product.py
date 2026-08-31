from fastapi import APIRouter, Depends, Query, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.api.dependencies import require_admin_or_owner
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    SetPriceRequest, SetPriceResponse,
    PricingResponse, PriceHistoryOut,
)
from app.services import product_service

router = APIRouter(tags=["Products"])


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("/", response_model=ProductOut, status_code=201,
             dependencies=[Depends(require_admin_or_owner)],
             summary="Buat produk baru — hpp_total dimulai dari 0, isi resep dulu")
async def create_product(data: ProductCreate, db: AsyncSession = Depends(get_db)):
    return await product_service.create_product(db, data)


@router.get("/", response_model=list[ProductOut],
            summary="List produk — filter by is_active / kategori")
async def list_products(
    only_active: bool = Query(False, description="True = hanya produk aktif (untuk Buyer Site)"),
    kategori: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await product_service.get_all_products(db, only_active, kategori)


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    p = await product_service.get_product_or_404(db, product_id)
    return ProductOut.model_validate(p)


@router.put("/{product_id}", response_model=ProductOut,
             dependencies=[Depends(require_admin_or_owner)],
             summary="Edit produk (Admin/Owner) — nama, deskripsi, kategori, is_active")
async def update_product(product_id: int, data: ProductUpdate, db: AsyncSession = Depends(get_db)):
    return await product_service.update_product(db, product_id, data)


@router.delete("/{product_id}",
               dependencies=[Depends(require_admin_or_owner)],
               summary="Hapus produk beserta semua resep dan riwayat harganya")
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    return await product_service.delete_product(db, product_id)


@router.post("/{product_id}/image", response_model=ProductOut,
             dependencies=[Depends(require_admin_or_owner)],
             summary="Upload foto produk (Admin/Owner) — format JPEG, PNG, WEBP, maks 5MB")
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(..., description="File gambar produk (JPEG/PNG/WEBP)"),
    db: AsyncSession = Depends(get_db),
):
    return await product_service.upload_product_image(db, product_id, file)


# ── Pricing — khusus Owner ────────────────────────────────────────────────────

@router.patch("/{product_id}/price", response_model=SetPriceResponse,
              dependencies=[Depends(require_admin_or_owner)],
              summary="Owner menetapkan harga jual — Use Case 2 (Set Product Prices). "
                      "Sistem beri warning jika harga < HPP. Riwayat perubahan dicatat otomatis.")
async def set_price(product_id: int, data: SetPriceRequest, db: AsyncSession = Depends(get_db)):
    return await product_service.set_product_price(db, product_id, data)


@router.get("/{product_id}/pricing", response_model=PricingResponse,
            summary="Lihat HPP breakdown per bahan + margin vs harga jual saat ini")
async def get_pricing(product_id: int, db: AsyncSession = Depends(get_db)):
    return await product_service.get_pricing_breakdown(db, product_id)


@router.get("/{product_id}/price-history", response_model=list[PriceHistoryOut],
            summary="Riwayat perubahan harga jual oleh Owner — Use Case 6 (View Price History)")
async def get_price_history(product_id: int, db: AsyncSession = Depends(get_db)):
    return await product_service.get_price_history(db, product_id)
