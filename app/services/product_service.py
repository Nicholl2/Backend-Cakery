from decimal import Decimal
import os
import anyio
import cloudinary
import cloudinary.uploader
from fastapi import HTTPException, status, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.repositories import product_repo, recipe_repo
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    SetPriceRequest, SetPriceResponse,
    PricingResponse, CostDetail, PriceHistoryOut,
)
from app.models.product import Product
from typing import Optional


async def create_product(db: AsyncSession, data: ProductCreate) -> ProductOut:
    result = await db.execute(select(Product).where(Product.nama_produk == data.nama_produk))
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Produk '{data.nama_produk}' sudah terdaftar.",
        )
    product = await product_repo.create(db, data)
    return ProductOut.model_validate(product)


async def get_all_products(
    db: AsyncSession,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[ProductOut]:
    products = await product_repo.get_all(db, only_active, kategori)
    return [ProductOut.model_validate(p) for p in products]


async def get_product_or_404(db: AsyncSession, product_id: int) -> Product:
    p = await product_repo.get_by_id(db, product_id)
    if not p:
        raise HTTPException(404, "Produk tidak ditemukan.")
    return p


async def update_product(db: AsyncSession, product_id: int, data: ProductUpdate) -> ProductOut:
    product = await get_product_or_404(db, product_id)
    updated = await product_repo.update(db, product, data)
    return ProductOut.model_validate(updated)


async def delete_product(db: AsyncSession, product_id: int) -> dict:
    product = await get_product_or_404(db, product_id)
    await product_repo.delete(db, product)
    return {"deleted": True, "product_id": product_id}


# ── Pricing ──────────────────────────────────────────────────────────────────

async def get_pricing_breakdown(db: AsyncSession, product_id: int) -> PricingResponse:
    """
    Tampilkan HPP detail per bahan — Use Case 6 / View Price History context.
    """
    product = await get_product_or_404(db, product_id)
    hpp, breakdown = await recipe_repo.calculate_hpp(db, product_id)

    margin = None
    warning = False
    if product.harga_jual:
        margin = float(
            (Decimal(str(product.harga_jual)) - hpp) / hpp * 100
        ) if hpp > 0 else None
        warning = Decimal(str(product.harga_jual)) < hpp

    return PricingResponse(
        product_id=product_id,
        nama_produk=product.nama_produk,
        hpp=hpp,
        harga_jual=product.harga_jual,
        margin_persen=margin,
        warning_below_hpp=warning,
        breakdown=[CostDetail(**b) for b in breakdown],
    )


async def set_product_price(
    db: AsyncSession, product_id: int, data: SetPriceRequest
) -> SetPriceResponse:
    """
    Owner menetapkan/mengubah harga jual produk — Use Case 2 (Set Product Prices).
    Sistem:
      1. Menampilkan perbandingan HPP vs harga_jual baru.
      2. Memberi peringatan jika harga_jual < HPP (tidak memblokir, hanya warning).
      3. Menyimpan perubahan + mencatat riwayat ke price_histories.
    """
    product = await get_product_or_404(db, product_id)

    warning = data.harga_jual < product.hpp_total

    await product_repo.set_price(db, product, data.harga_jual, data.changed_by)

    margin = None
    if product.hpp_total and product.hpp_total > 0:
        margin = float(
            (data.harga_jual - product.hpp_total) / product.hpp_total * 100
        )

    return SetPriceResponse(
        product_id=product_id,
        nama_produk=product.nama_produk,
        hpp_total=product.hpp_total,
        harga_jual_baru=data.harga_jual,
        margin_persen=margin,
        warning_below_hpp=warning,
    )


async def get_price_history(db: AsyncSession, product_id: int) -> list[PriceHistoryOut]:
    await get_product_or_404(db, product_id)
    history = await product_repo.get_price_history(db, product_id)
    return [PriceHistoryOut.model_validate(h) for h in history]


from app.utils.cloudinary_helper import upload_image_to_cloudinary

async def upload_product_image(
    db: AsyncSession,
    product_id: int,
    file: UploadFile
) -> ProductOut:
    """
    Upload product image to Cloudinary: validates type and size,
    uploads directly from memory stream (file.file), and saves secure HTTPS URL to DB.
    """
    # 1. Ambil data produk, error 404 jika tidak ditemukan
    product = await get_product_or_404(db, product_id)

    # 2. Upload via helper Cloudinary
    secure_url = await upload_image_to_cloudinary(file, folder="toti-cakery/products")

    # 3. Update database dengan URL Cloudinary
    updated_product = await product_repo.update_image_url(db, product, secure_url)
    return ProductOut.model_validate(updated_product)


