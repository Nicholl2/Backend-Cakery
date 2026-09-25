from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, update, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.product import Product, ProductImage
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
        .options(
            selectinload(Product.category_rel),
            selectinload(Product.recipes).selectinload(Recipe.stock_item),
            selectinload(Product.price_histories),
            selectinload(Product.images),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


async def get_all(
    db: AsyncSession,
    only_active: bool = False,
    kategori: Optional[str] = None,
) -> list[Product]:
    q = select(Product).options(
        selectinload(Product.category_rel),
        selectinload(Product.recipes).selectinload(Recipe.stock_item),
        selectinload(Product.images),
    ).execution_options(populate_existing=True)
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
    product.hpp_total = hpp_total
    product.harga_jual = harga_jual
    await db.commit()
    await db.refresh(product)
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


# ── PRODUCT IMAGES OPERATIONS ────────────────────────────────────────────────

async def get_product_images(db: AsyncSession, product_id: int) -> list[ProductImage]:
    result = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.is_primary.desc(), ProductImage.id.asc())
    )
    return result.scalars().all()


async def add_product_images(
    db: AsyncSession,
    product_id: int,
    image_urls: list[str],
    primary_index: int = 0,
) -> list[ProductImage]:
    # Ambil gambar produk yang sudah ada
    existing_result = await db.execute(
        select(ProductImage).where(ProductImage.product_id == product_id)
    )
    existing_images = existing_result.scalars().all()
    has_primary = any(img.is_primary for img in existing_images)

    new_images: list[ProductImage] = []
    for idx, url in enumerate(image_urls):
        is_primary = False
        if not has_primary and idx == primary_index:
            is_primary = True
            has_primary = True
        elif primary_index == idx and not existing_images:
            is_primary = True

        img = ProductImage(
            product_id=product_id,
            image_url=url,
            is_primary=is_primary,
        )
        db.add(img)
        new_images.append(img)

    await db.commit()
    for img in new_images:
        await db.refresh(img)

    primary_res = await db.execute(
        select(ProductImage)
        .where(ProductImage.product_id == product_id, ProductImage.is_primary == True)
        .limit(1)
    )
    primary_img = primary_res.scalars().first()
    if primary_img:
        prod_res = await db.execute(select(Product).where(Product.id == product_id))
        prod = prod_res.scalars().first()
        if prod:
            prod.image_url = primary_img.image_url
            await db.commit()

    return new_images


async def set_primary_image(
    db: AsyncSession,
    product_id: int,
    image_id: int,
) -> Optional[ProductImage]:
    result = await db.execute(
        select(ProductImage).where(ProductImage.product_id == product_id)
    )
    images = result.scalars().all()
    target_img = None
    for img in images:
        if img.id == image_id:
            img.is_primary = True
            target_img = img
        else:
            img.is_primary = False

    if target_img:
        # Sync products.image_url
        prod_res = await db.execute(select(Product).where(Product.id == product_id))
        prod = prod_res.scalars().first()
        if prod:
            prod.image_url = target_img.image_url
        await db.commit()
        await db.refresh(target_img)
        return target_img


async def delete_product_image(
    db: AsyncSession,
    product_id: int,
    image_id: int,
) -> bool:
    result = await db.execute(
        select(ProductImage).where(
            ProductImage.id == image_id,
            ProductImage.product_id == product_id,
        )
    )
    img = result.scalars().first()
    if not img:
        return False

    was_primary = img.is_primary
    await db.delete(img)
    await db.commit()

    if was_primary:
        # Reassign primary to first remaining image if exists
        remaining_res = await db.execute(
            select(ProductImage)
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.id.asc())
        )
        remaining = remaining_res.scalars().first()
        prod_res = await db.execute(select(Product).where(Product.id == product_id))
        prod = prod_res.scalars().first()
        if remaining:
            remaining.is_primary = True
            if prod:
                prod.image_url = remaining.image_url
            await db.commit()
        else:
            if prod:
                prod.image_url = None
            await db.commit()

    return True