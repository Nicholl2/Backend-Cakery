from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.api.dependencies import require_admin_or_owner, require_internal_user, get_current_user_id
from app.schemas.stock import StockCreate, StockUpdate, StockOut, StockAdjustmentCreate, StockMovementOut
from app.services import stock_service

router = APIRouter(tags=["Stock Items"])


@router.post("/", response_model=StockOut, status_code=201,
             dependencies=[Depends(require_admin_or_owner)],
             summary="Tambah bahan baku atau kemasan baru")
async def create_stock(data: StockCreate, user_id: int = Depends(get_current_user_id),
                       db: AsyncSession = Depends(get_db)):
    return await stock_service.create_stock(db, data, user_id)


@router.get("/", response_model=list[StockOut],
            dependencies=[Depends(require_internal_user)],
            summary="List semua stok — bisa filter by kategori (bahan_baku / kemasan)")
async def list_stock(
    kategori: Optional[str] = Query(None, description="bahan_baku | kemasan"),
    db: AsyncSession = Depends(get_db),
):
    return await stock_service.get_all_stock(db, kategori)


@router.get("/{stock_id}", response_model=StockOut,
            dependencies=[Depends(require_internal_user)])
async def get_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    return await stock_service.get_stock_or_404(db, stock_id)


@router.put("/{stock_id}", response_model=StockOut,
            dependencies=[Depends(require_admin_or_owner)],
            summary="Edit data bahan (nama, satuan, kategori, harga, stok)")
async def update_stock(stock_id: int, data: StockUpdate, db: AsyncSession = Depends(get_db)):
    return await stock_service.update_stock(db, stock_id, data)


@router.delete("/{stock_id}",
               dependencies=[Depends(require_admin_or_owner)],
               summary="Hapus bahan — gagal jika masih dipakai di resep")
async def delete_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    return await stock_service.delete_stock(db, stock_id)


@router.post("/{stock_id}/adjustments", response_model=StockOut,
             dependencies=[Depends(require_admin_or_owner)], status_code=201)
async def adjust_stock(stock_id: int, data: StockAdjustmentCreate,
                       user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    return await stock_service.adjust_stock(db, stock_id, data, user_id)


@router.get("/{stock_id}/movements", response_model=list[StockMovementOut],
            dependencies=[Depends(require_internal_user)])
async def list_stock_movements(stock_id: int, db: AsyncSession = Depends(get_db)):
    return await stock_service.get_stock_movements(db, stock_id)
