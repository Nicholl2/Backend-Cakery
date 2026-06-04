from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.repositories import product_repo, recipe_repo
from app.schemas.product import (
    ProductCreate, ProductUpdate, ProductOut,
    SetPriceRequest, SetPriceResponse,
    PricingResponse, CostDetail, PriceHistoryOut,
)
from app.models.product import Product
from typing import Optional


def create_product(db: Session, data: ProductCreate) -> ProductOut:
    existing = db.query(Product).filter(Product.nama_produk == data.nama_produk).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Produk '{data.nama_produk}' sudah terdaftar.",
        )
    product = product_repo.create(db, data)
    return ProductOut.model_validate(product)


def get_all_products(
    db: Session,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[ProductOut]:
    products = product_repo.get_all(db, only_active, kategori)
    return [ProductOut.model_validate(p) for p in products]


def get_product_or_404(db: Session, product_id: int) -> Product:
    p = product_repo.get_by_id(db, product_id)
    if not p:
        raise HTTPException(404, "Produk tidak ditemukan.")
    return p


def update_product(db: Session, product_id: int, data: ProductUpdate) -> ProductOut:
    product = get_product_or_404(db, product_id)
    updated = product_repo.update(db, product, data)
    return ProductOut.model_validate(updated)


def delete_product(db: Session, product_id: int) -> dict:
    product = get_product_or_404(db, product_id)
    product_repo.delete(db, product)
    return {"deleted": True, "product_id": product_id}


# ── Pricing ──────────────────────────────────────────────────────────────────

def get_pricing_breakdown(db: Session, product_id: int) -> PricingResponse:
    """
    Tampilkan HPP detail per bahan — Use Case 6 / View Price History context.
    """
    product = get_product_or_404(db, product_id)
    hpp, breakdown = recipe_repo.calculate_hpp(db, product_id)

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


def set_product_price(
    db: Session, product_id: int, data: SetPriceRequest
) -> SetPriceResponse:
    """
    Owner menetapkan/mengubah harga jual produk — Use Case 2 (Set Product Prices).
    Sistem:
      1. Menampilkan perbandingan HPP vs harga_jual baru.
      2. Memberi peringatan jika harga_jual < HPP (tidak memblokir, hanya warning).
      3. Menyimpan perubahan + mencatat riwayat ke price_histories.
    """
    product = get_product_or_404(db, product_id)

    warning = data.harga_jual < product.hpp_total

    product_repo.set_price(db, product, data.harga_jual, data.changed_by)

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


def get_price_history(db: Session, product_id: int) -> list[PriceHistoryOut]:
    get_product_or_404(db, product_id)
    history = product_repo.get_price_history(db, product_id)
    return [PriceHistoryOut.model_validate(h) for h in history]