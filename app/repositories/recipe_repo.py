from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
from app.models.product import Product
from typing import Optional


# ── Query dasar ──────────────────────────────────────────────────────────────

async def get_by_product(db: AsyncSession, product_id: int) -> list[Recipe]:
    result = await db.execute(
        select(Recipe)
        .where(Recipe.product_id == product_id)
        .options(selectinload(Recipe.stock_item))
    )
    return result.scalars().all()


async def get_by_id(db: AsyncSession, recipe_id: int) -> Optional[Recipe]:
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    return result.scalars().first()


async def get_by_product_and_stock(
    db: AsyncSession, product_id: int, stock_item_id: int
) -> Optional[Recipe]:
    result = await db.execute(
        select(Recipe)
        .where(
            Recipe.product_id == product_id,
            Recipe.stock_item_id == stock_item_id,
        )
    )
    return result.scalars().first()


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def create(db: AsyncSession, product_id: int, stock_item_id: int, jumlah: Decimal) -> Recipe:
    recipe = Recipe(
        product_id=product_id,
        stock_item_id=stock_item_id,
        jumlah_dibutuhkan=jumlah,
    )
    db.add(recipe)
    await db.flush()   # dapat id sebelum commit
    return recipe


async def update_qty(db: AsyncSession, recipe: Recipe, jumlah: Decimal) -> Recipe:
    recipe.jumlah_dibutuhkan = jumlah
    await db.flush()
    return recipe


async def delete(db: AsyncSession, recipe: Recipe) -> None:
    await db.delete(recipe)
    await db.flush()


# ── HPP Calculation ──────────────────────────────────────────────────────────

async def calculate_hpp(db: AsyncSession, product_id: int) -> tuple[Decimal, list[dict]]:
    """
    Hitung HPP dari seluruh bahan dalam resep produk.
    Menggunakan harga_per_satuan terkini (average costing) dari stock_items.

    Returns:
        (hpp_total, breakdown_list)
    """
    result = await db.execute(
        select(
            Recipe.jumlah_dibutuhkan,
            StockItem.harga_per_satuan,
            StockItem.nama_item,
            StockItem.satuan,
            Recipe.id.label("recipe_id"),
            Recipe.stock_item_id,
        )
        .join(StockItem, Recipe.stock_item_id == StockItem.id)
        .where(Recipe.product_id == product_id)
    )
    rows = result.all()

    total = Decimal("0")
    detail = []

    for r in rows:
        cost = Decimal(str(r.jumlah_dibutuhkan)) * Decimal(str(r.harga_per_satuan))
        total += cost
        detail.append({
            "bahan": r.nama_item,
            "satuan": r.satuan,
            "qty": float(r.jumlah_dibutuhkan),
            "unit_price": float(r.harga_per_satuan),
            "cost": float(cost),
        })

    return total, detail


async def sync_hpp_to_product(db: AsyncSession, product_id: int) -> Decimal:
    """
    Hitung ulang HPP lalu simpan ke products.hpp_total.
    Dipanggil setiap kali resep berubah ATAU harga bahan berubah.
    """
    hpp, _ = await calculate_hpp(db, product_id)
    await db.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(hpp_total=hpp)
    )
    return hpp
