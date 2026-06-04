from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.stock_item import StockItem
from app.schemas.stock import StockCreate, StockUpdate
from typing import Optional


async def create(db: AsyncSession, data: StockCreate) -> StockItem:
    item = StockItem(**data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def get_by_id(db: AsyncSession, stock_id: int) -> Optional[StockItem]:
    result = await db.execute(
        select(StockItem)
        .where(StockItem.id == stock_id)
        .options(selectinload(StockItem.recipes))
    )
    return result.scalars().first()


async def get_all(db: AsyncSession, kategori: Optional[str] = None) -> list[StockItem]:
    q = select(StockItem)
    if kategori:
        q = q.where(StockItem.kategori == kategori)
    result = await db.execute(q.order_by(StockItem.nama_item))
    return result.scalars().all()


async def update(db: AsyncSession, item: StockItem, data: StockUpdate) -> StockItem:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.commit()
    await db.refresh(item)
    return item


async def delete(db: AsyncSession, item: StockItem) -> bool:
    await db.delete(item)
    await db.commit()
    return True


async def update_average_cost(
    db: AsyncSession,
    stock_id: int,
    qty_masuk: float,
    harga_beli_total: float,
) -> StockItem:
    """
    Average Costing — spek bagian 2.3.1 poin 2 & Use Case 8 (Record Purchases).

    Rumus:
        harga_baru = (stok_lama * harga_lama + qty_masuk * harga_satuan_baru)
                     / (stok_lama + qty_masuk)
    """
    result = await db.execute(
        select(StockItem)
        .where(StockItem.id == stock_id)
        .with_for_update()
    )
    item = result.scalars().first()
    if not item:
        return None

    harga_satuan_baru = harga_beli_total / qty_masuk if qty_masuk else 0

    stok_lama = float(item.stok_tersedia)
    harga_lama = float(item.harga_per_satuan)

    if stok_lama + qty_masuk > 0:
        harga_rata_rata = (
            (stok_lama * harga_lama) + (qty_masuk * harga_satuan_baru)
        ) / (stok_lama + qty_masuk)
    else:
        harga_rata_rata = harga_satuan_baru

    item.harga_per_satuan = round(harga_rata_rata, 4)
    item.stok_tersedia = stok_lama + qty_masuk
    item.version += 1

    await db.commit()
    await db.refresh(item)
    return item
