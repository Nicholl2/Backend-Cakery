from decimal import Decimal
import os
import anyio
from fastapi import HTTPException, status, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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


async def upload_product_image(
    db: AsyncSession,
    product_id: int,
    file: UploadFile
) -> ProductOut:
    """
    Upload product image: validates type and size, saves file asynchronously,
    and updates image_url to the relative path in the database.
    """
    # 1. Ambil data produk, error 404 jika tidak ditemukan
    product = await get_product_or_404(db, product_id)

    # 2. Validasi tipe file (Content-Type)
    ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tipe file tidak didukung. Hanya JPEG, PNG, dan WEBP yang diperbolehkan."
        )

    # 3. Validasi ukuran file (Maksimal 5 MB)
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ukuran file melebihi batas maksimal 5 MB."
        )

    # 4. Ambil ekstensi berkas secara aman
    filename = file.filename or ""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    if not ext:
        content_type_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp"
        }
        ext = content_type_map.get(file.content_type, ".jpg")

    # 5. Definisikan relative dan absolute paths
    relative_path = f"/static/products/{product_id}{ext}"
    dest_dir = "static/products"
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"{product_id}{ext}")

    # 6. Hapus berkas lama di disk jika tipenya/ekstensinya berbeda untuk menghindari berkas yatim (orphan)
    if product.image_url:
        old_rel_path = product.image_url.lstrip("/")
        if os.path.exists(old_rel_path) and old_rel_path != dest_path:
            try:
                os.remove(old_rel_path)
            except Exception:
                pass

    # 7. Simpan file secara asinkronus menggunakan anyio
    content = await file.read()
    async with await anyio.open_file(dest_path, "wb") as f:
        await f.write(content)

    # 8. Update database
    updated_product = await product_repo.update_image_url(db, product, relative_path)
    return ProductOut.model_validate(updated_product)

