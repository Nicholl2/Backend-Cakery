from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.product import Product
from app.models.price_history import PriceHistory
from app.schemas.product import ProductCreate, ProductUpdate
from typing import Optional


def create(db: Session, data: ProductCreate) -> Product:
    product = Product(**data.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_by_id(db: Session, product_id: int) -> Optional[Product]:
    return db.query(Product).filter(Product.id == product_id).first()


def get_all(
    db: Session,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[Product]:
    q = db.query(Product)
    if only_active:
        q = q.filter(Product.is_active == True)
    if kategori:
        q = q.filter(Product.kategori == kategori)
    return q.order_by(Product.nama_produk).all()


def update(db: Session, product: Product, data: ProductUpdate) -> Product:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


def delete(db: Session, product: Product) -> bool:
    db.delete(product)
    db.commit()
    return True


def set_price(
    db: Session,
    product: Product,
    harga_jual_baru: Decimal,
    changed_by: Optional[str] = None,
) -> Product:
    """
    Owner menetapkan harga jual produk — Use Case 2 (Set Product Prices).
    Sistem otomatis mencatat riwayat perubahan ke tabel price_histories.
    """
    history = PriceHistory(
        product_id=product.id,
        harga_jual_lama=product.harga_jual,
        harga_jual_baru=harga_jual_baru,
        hpp_saat_itu=product.hpp_total,
        changed_by=changed_by,
    )
    db.add(history)

    product.harga_jual = harga_jual_baru
    db.commit()
    db.refresh(product)
    return product


def get_price_history(db: Session, product_id: int) -> list[PriceHistory]:
    return (
        db.query(PriceHistory)
        .filter(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.created_at.desc())
        .all()
    )