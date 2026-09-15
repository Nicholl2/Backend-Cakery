from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from decimal import Decimal, ROUND_HALF_UP
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
    qty_masuk: Decimal | float,
    harga_beli_total: Decimal | float,
    commit: bool = True,
) -> Optional[StockItem]:
    """
    Weighted Average Costing & Inventory Addition.
    Tambahkan jumlah barang yang diterima ke stok_tersedia dan
    hitung average cost baru dengan presisi Decimal.

    Rumus:
        harga_rata_rata = (stok_lama * harga_lama + qty_masuk * harga_satuan_baru)
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

    qty_in = Decimal(str(qty_masuk))
    total_cost_in = Decimal(str(harga_beli_total))
    harga_satuan_baru = (total_cost_in / qty_in) if qty_in > 0 else Decimal("0")

    stok_lama = Decimal(str(item.stok_tersedia or "0"))
    harga_lama = Decimal(str(item.harga_per_satuan or "0"))

    new_stok = stok_lama + qty_in

    if new_stok > 0:
        harga_rata_rata = (
            (stok_lama * harga_lama) + (qty_in * harga_satuan_baru)
        ) / new_stok
    else:
        harga_rata_rata = harga_satuan_baru

    item.harga_per_satuan = harga_rata_rata.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    item.stok_tersedia = new_stok.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    item.version += 1

    if commit:
        await db.commit()
        await db.refresh(item)
    else:
        await db.flush()

    return item
