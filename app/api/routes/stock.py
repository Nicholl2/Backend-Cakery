from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.schemas.stock import StockCreate, StockUpdate, StockOut
from app.services import stock_service

router = APIRouter( tags=["Stock Items"])


@router.post("/", response_model=StockOut, status_code=201,
             summary="Tambah bahan baku atau kemasan baru")
async def create_stock(data: StockCreate, db: AsyncSession = Depends(get_db)):
    return await stock_service.create_stock(db, data)


@router.get("/", response_model=list[StockOut],
            summary="List semua stok — bisa filter by kategori (bahan_baku / kemasan)")
async def list_stock(
    kategori: Optional[str] = Query(None, description="bahan_baku | kemasan"),
    db: AsyncSession = Depends(get_db),
):
    return await stock_service.get_all_stock(db, kategori)


@router.get("/{stock_id}", response_model=StockOut)
async def get_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    return await stock_service.get_stock_or_404(db, stock_id)


@router.put("/{stock_id}", response_model=StockOut,
            summary="Edit data bahan (nama, satuan, kategori, harga, stok)")
async def update_stock(stock_id: int, data: StockUpdate, db: AsyncSession = Depends(get_db)):
    return await stock_service.update_stock(db, stock_id, data)


@router.delete("/{stock_id}", summary="Hapus bahan — gagal jika masih dipakai di resep")
async def delete_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    return await stock_service.delete_stock(db, stock_id)
