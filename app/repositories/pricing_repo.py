from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.recipe import Recipe
from app.models.stock_item import StockItem


async def get_recipe_with_cost(db: AsyncSession, product_id: int):
    stmt = (
        select(
            Recipe.jumlah_dibutuhkan,
            StockItem.harga_per_satuan,
            StockItem.nama_item.label("nama_bahan"),
        )
        .join(StockItem, Recipe.stock_item_id == StockItem.id)
        .where(Recipe.product_id == product_id)
    )
    result = await db.execute(stmt)
    return result.all()

