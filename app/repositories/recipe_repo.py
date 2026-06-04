from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
from app.models.product import Product
from typing import Optional


# ── Query dasar ──────────────────────────────────────────────────────────────

def get_by_product(db: Session, product_id: int) -> list[Recipe]:
    return (
        db.query(Recipe)
        .filter(Recipe.product_id == product_id)
        .all()
    )


def get_by_id(db: Session, recipe_id: int) -> Optional[Recipe]:
    return db.query(Recipe).filter(Recipe.id == recipe_id).first()


def get_by_product_and_stock(
    db: Session, product_id: int, stock_item_id: int
) -> Optional[Recipe]:
    return (
        db.query(Recipe)
        .filter(
            Recipe.product_id == product_id,
            Recipe.stock_item_id == stock_item_id,
        )
        .first()
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────

def create(db: Session, product_id: int, stock_item_id: int, jumlah: Decimal) -> Recipe:
    recipe = Recipe(
        product_id=product_id,
        stock_item_id=stock_item_id,
        jumlah_dibutuhkan=jumlah,
    )
    db.add(recipe)
    db.flush()   # dapat id sebelum commit
    return recipe


def update_qty(db: Session, recipe: Recipe, jumlah: Decimal) -> Recipe:
    recipe.jumlah_dibutuhkan = jumlah
    db.flush()
    return recipe


def delete(db: Session, recipe: Recipe) -> None:
    db.delete(recipe)
    db.flush()


# ── HPP Calculation ──────────────────────────────────────────────────────────

def calculate_hpp(db: Session, product_id: int) -> tuple[Decimal, list[dict]]:
    """
    Hitung HPP dari seluruh bahan dalam resep produk.
    Menggunakan harga_per_satuan terkini (average costing) dari stock_items.

    Returns:
        (hpp_total, breakdown_list)
    """
    rows = (
        db.query(
            Recipe.jumlah_dibutuhkan,
            StockItem.harga_per_satuan,
            StockItem.nama_item,
            StockItem.satuan,
            Recipe.id.label("recipe_id"),
            Recipe.stock_item_id,
        )
        .join(StockItem, Recipe.stock_item_id == StockItem.id)
        .filter(Recipe.product_id == product_id)
        .all()
    )

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


def sync_hpp_to_product(db: Session, product_id: int) -> Decimal:
    """
    Hitung ulang HPP lalu simpan ke products.hpp_total.
    Dipanggil setiap kali resep berubah ATAU harga bahan berubah.
    """
    hpp, _ = calculate_hpp(db, product_id)
    db.query(Product).filter(Product.id == product_id).update(
        {"hpp_total": hpp},
        synchronize_session="fetch",
    )
    return hpp