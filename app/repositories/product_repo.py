from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.product import Product
from app.models.price_history import PriceHistory
from app.schemas.product import ProductCreate, ProductUpdate
from typing import Optional


async def create(db: AsyncSession, data: ProductCreate) -> Product:
    product = Product(**data.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


async def get_by_id(db: AsyncSession, product_id: int) -> Optional[Product]:
    result = await db.execute(select(Product).where(Product.id == product_id))
    return result.scalars().first()


async def get_all(
    db: AsyncSession,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[Product]:
    q = select(Product)
    if only_active:
        q = q.where(Product.is_active == True)
    if kategori:
        q = q.where(Product.kategori == kategori)
    result = await db.execute(q.order_by(Product.nama_produk))
    return result.scalars().all()


async def update(db: AsyncSession, product: Product, data: ProductUpdate) -> Product:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await db.commit()
    await db.refresh(product)
    return product


async def delete(db: AsyncSession, product: Product) -> bool:
    await db.delete(product)
    await db.commit()
    return True


async def set_price(
    db: AsyncSession,
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
    await db.commit()
    await db.refresh(product)
    return product


async def get_price_history(db: AsyncSession, product_id: int) -> list[PriceHistory]:
    result = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.created_at.desc())
    )
    return result.scalars().all()
