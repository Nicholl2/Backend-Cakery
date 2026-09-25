from decimal import Decimal
import os
import anyio
import cloudinary
import cloudinary.uploader
from fastapi import HTTPException, status, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.repositories import product_repo, recipe_repo, category_repo
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    SetPriceRequest, SetPriceResponse,
    PricingResponse, CostDetail, PriceHistoryOut,
)
from app.models.product import Product, Category
from app.schemas.product import CategoryCreate, CategoryUpdate, CategoryResponse
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
    only_available: bool = False,
) -> list[ProductOut]:
    products = await product_repo.get_all(db, only_active, kategori)
    serialized = [ProductOut.model_validate(p) for p in products]
    if only_available:
        return [p for p in serialized if p.is_in_stock]
    return serialized


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


from app.utils.cloudinary_helper import upload_image_to_cloudinary, delete_image_from_cloudinary
import asyncio

async def upload_product_images(
    db: AsyncSession,
    product_id: int,
    files: list[UploadFile],
    primary_index: int = 0,
) -> ProductOut:
    """
    Upload multiple product images to Cloudinary in parallel,
    save URLs to product_images table, and set one as primary.
    """
    # 1. Validasi keberadaan file
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Minimal 1 file gambar harus diunggah.",
        )

    # 2. Ambil data produk, error 404 jika tidak ditemukan
    product = await get_product_or_404(db, product_id)

    # 3. Upload paralel ke Cloudinary menggunakan asyncio.gather
    upload_tasks = [
        upload_image_to_cloudinary(f, folder="toti-cakery/products")
        for f in files
    ]
    secure_urls = await asyncio.gather(*upload_tasks)

    # 4. Simpan ke database via repository
    await product_repo.add_product_images(
        db=db,
        product_id=product.id,
        image_urls=secure_urls,
        primary_index=primary_index,
    )

    # 5. Ambil data produk terbaru beserta relasi images
    updated_product = await get_product_or_404(db, product_id)
    return ProductOut.model_validate(updated_product)


async def upload_product_image(
    db: AsyncSession,
    product_id: int,
    file: UploadFile
) -> ProductOut:
    """
    Single image upload (Backward compatibility):
    Delegates to upload_product_images with a single item list.
    """
    return await upload_product_images(db, product_id, [file], primary_index=0)


async def set_product_primary_image(
    db: AsyncSession,
    product_id: int,
    image_id: int,
) -> ProductOut:
    """
    Set one product image as the primary image.
    """
    await get_product_or_404(db, product_id)
    target = await product_repo.set_primary_image(db, product_id, image_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gambar produk tidak ditemukan.",
        )
    fresh_product = await get_product_or_404(db, product_id)
    return ProductOut.model_validate(fresh_product)


async def delete_product_image(
    db: AsyncSession,
    product_id: int,
    image_id: int,
) -> dict:
    """
    Delete a single product image, remove from Cloudinary, and reassign primary if necessary.
    """
    await get_product_or_404(db, product_id)
    deleted_url = await product_repo.delete_product_image(db, product_id, image_id)
    if deleted_url is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Gambar produk tidak ditemukan.",
        )
    # Async background/best-effort delete from Cloudinary
    if deleted_url:
        await delete_image_from_cloudinary(deleted_url)

    return {"deleted": True, "product_id": product_id, "image_id": image_id}



# ── CATEGORY ─────────────────────────────────────────────────────────────────
async def create_category(db: AsyncSession, data: CategoryCreate) -> CategoryResponse:
    existing = await category_repo.get_by_name(db, data.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Kategori '{data.name}' sudah ada.",
        )
    cat = await category_repo.create(db, data)
    return CategoryResponse.model_validate(cat)

async def get_all_categories(db: AsyncSession) -> list[CategoryResponse]:
    categories = await category_repo.get_all(db)
    return [CategoryResponse.model_validate(c) for c in categories]

async def update_category(db: AsyncSession, category_id: int, data: CategoryUpdate) -> CategoryResponse:
    cat = await category_repo.get_by_id(db, category_id)
    if not cat:
        raise HTTPException(404, "Kategori tidak ditemukan.")
    
    if data.name:
        existing = await category_repo.get_by_name(db, data.name)
        if existing and existing.id != category_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Kategori '{data.name}' sudah ada.",
            )
    
    updated = await category_repo.update(db, cat, data)
    return CategoryResponse.model_validate(updated)

async def delete_category(db: AsyncSession, category_id: int) -> dict:
    cat = await category_repo.get_by_id(db, category_id)
    if not cat:
        raise HTTPException(404, "Kategori tidak ditemukan.")
    await category_repo.delete(db, cat)
    return {"deleted": True, "category_id": category_id}

