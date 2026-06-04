from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.stock_item import StockItem
from app.schemas.stock import StockCreate, StockUpdate
from app.repositories import stock_repo
from typing import Optional


async def create_stock(db: AsyncSession, data: StockCreate) -> StockItem:
    # Cek duplikat nama
    result = await db.execute(select(StockItem).where(StockItem.nama_item == data.nama_item))
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item '{data.nama_item}' sudah terdaftar di database.",
        )
    return await stock_repo.create(db, data)


async def get_all_stock(db: AsyncSession, kategori: Optional[str] = None) -> list[StockItem]:
    return await stock_repo.get_all(db, kategori)


async def get_stock_or_404(db: AsyncSession, stock_id: int) -> StockItem:
    item = await stock_repo.get_by_id(db, stock_id)
    if not item:
        raise HTTPException(status_code=404, detail="Stock item tidak ditemukan.")
    return item


async def update_stock(db: AsyncSession, stock_id: int, data: StockUpdate) -> StockItem:
    item = await get_stock_or_404(db, stock_id)
    return await stock_repo.update(db, item, data)


async def delete_stock(db: AsyncSession, stock_id: int) -> bool:
    item = await get_stock_or_404(db, stock_id)

    # Cegah hapus bahan yang masih dipakai di resep
    if item.recipes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Item '{item.nama_item}' masih digunakan dalam {len(item.recipes)} resep. "
                "Hapus resep yang menggunakannya terlebih dahulu."
            ),
        )
    return await stock_repo.delete(db, item)
