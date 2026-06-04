from sqlalchemy.orm import Session
from app.models.recipe import Recipe
from app.models.stock_item import StockItem


def get_recipe_with_cost(db: Session, product_id: int):
    rows = (
        db.query(
            Recipe.jumlah_dibutuhkan,
            StockItem.harga_per_satuan,
            StockItem.nama_bahan
        )
        .join(StockItem, Recipe.stock_item_id == StockItem.id)
        .filter(Recipe.product_id == product_id)
        .all()
    )

    return rows
