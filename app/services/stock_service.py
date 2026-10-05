from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.stock_item import StockItem, StockMovement
from app.schemas.stock import StockCreate, StockUpdate, StockAdjustmentCreate
from app.repositories import stock_repo
from app.models.recipe import Recipe
from app.repositories import product_repo
from typing import Optional


async def create_stock(db: AsyncSession, data: StockCreate, user_id: int | None = None) -> StockItem:
    # Cek duplikat nama
    result = await db.execute(select(StockItem).where(func.lower(func.trim(StockItem.nama_item)) == data.nama_item.strip().lower()))
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item '{data.nama_item}' sudah terdaftar di database.",
        )
    return await stock_repo.create(db, data, user_id)


async def get_all_stock(db: AsyncSession, kategori: Optional[str] = None) -> list[StockItem]:
    return await stock_repo.get_all(db, kategori)


async def get_stock_or_404(db: AsyncSession, stock_id: int) -> StockItem:
    item = await stock_repo.get_by_id(db, stock_id)
    if not item:
        raise HTTPException(status_code=404, detail="Stock item tidak ditemukan.")
    return item


async def update_stock(db: AsyncSession, stock_id: int, data: StockUpdate) -> StockItem:
    item = await get_stock_or_404(db, stock_id)
    changes = data.model_dump(exclude_unset=True)
    if "nama_item" in changes:
        result = await db.execute(select(StockItem).where(
            StockItem.id != stock_id,
            func.lower(func.trim(StockItem.nama_item)) == changes["nama_item"].strip().lower()))
        if result.scalars().first():
            raise HTTPException(409, "Material dengan nama tersebut sudah terdaftar.")
    return await stock_repo.update(db, item, data)


async def adjust_stock(db: AsyncSession, stock_id: int, data: StockAdjustmentCreate, user_id: int) -> StockItem:
    result = await db.execute(select(StockItem).where(StockItem.id == stock_id).with_for_update())
    item = result.scalars().first()
    if not item:
        raise HTTPException(404, "Stock item tidak ditemukan.")
    delta = data.quantity_difference
    old_qty = Decimal(str(item.stok_tersedia))
    new_qty = old_qty + delta
    if new_qty < 0:
        raise HTTPException(409, "Penyesuaian tidak dapat membuat stok negatif.")
    old_cost = Decimal(str(item.harga_per_satuan))
    cost = data.unit_cost if delta > 0 else old_cost
    if delta > 0 and new_qty > 0:
        item.harga_per_satuan = ((old_qty * old_cost + delta * cost) / new_qty).quantize(Decimal("0.0001"))
    item.stok_tersedia = new_qty
    item.version += 1
    db.add(StockMovement(stock_item_id=item.id,
                         movement_type="ADJUSTMENT_IN" if delta > 0 else "ADJUSTMENT_OUT",
                         quantity=delta, unit_cost=cost, reference_type="adjustment",
                         created_by=user_id, reason=data.reason))
    rows = await db.execute(select(Recipe.product_id).where(Recipe.stock_item_id == item.id).distinct())
    for (product_id,) in rows.all():
        await product_repo.calculate_and_update_product_price(db, product_id, commit=False)
    await db.commit()
    await db.refresh(item)
    return item


async def get_stock_movements(db: AsyncSession, stock_id: int):
    await get_stock_or_404(db, stock_id)
    return await stock_repo.get_movements(db, stock_id)


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
    movements = await stock_repo.get_movements(db, item.id)
    if movements or Decimal(str(item.stok_tersedia)) != 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Material dengan saldo atau histori movement tidak dapat dihapus agar audit trail tetap utuh.",
        )
    return await stock_repo.delete(db, item)

