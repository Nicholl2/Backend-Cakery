from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
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
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.recipes).selectinload(Recipe.stock_item))
    )
    return result.scalars().first()


async def get_all(
    db: AsyncSession,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[Product]:
    q = select(Product).options(selectinload(Product.recipes).selectinload(Recipe.stock_item))
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

async def calculate_and_update_product_price(
    db: AsyncSession,
    product_id: int,
) -> Optional[Product]:
    """
    Hitung ulang HPP + harga_jual lalu simpan ke DB.
    Sumber harga: harga_per_satuan terkini dari stock_items (average costing).
    Dipanggil setiap kali resep atau harga bahan berubah.
    """
    # 1. Ambil product
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if not product:
        return None

    # 2. Hitung HPP dari resep × harga_per_satuan terkini
    hpp_result = await db.execute(
        select(
            func.sum(Recipe.jumlah_dibutuhkan * StockItem.harga_per_satuan)
        )
        .join(StockItem, Recipe.stock_item_id == StockItem.id)
        .where(Recipe.product_id == product_id)
    )
    hpp_total = hpp_result.scalar() or Decimal("0")
    hpp_total = Decimal(str(hpp_total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # 3. Hitung harga_jual jika markup tersedia
    markup = product.markup_percentage or Decimal("0")
    harga_jual = (hpp_total * (Decimal("1") + Decimal(str(markup)))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) if markup else product.harga_jual  # jaga harga manual jika markup = 0

    # 4. Update & commit
    await db.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(hpp_total=hpp_total, harga_jual=harga_jual)
    )
    await db.commit()

    product.hpp_total = hpp_total
    product.harga_jual = harga_jual
    return product


async def update_image_url(
    db: AsyncSession,
    product: Product,
    image_url: Optional[str]
) -> Product:
    product.image_url = image_url
    await db.commit()
    await db.refresh(product)
    return product