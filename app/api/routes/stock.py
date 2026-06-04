from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.schemas.stock import StockCreate, StockUpdate, StockOut
from app.services import stock_service

router = APIRouter(prefix="/stock-items", tags=["Stock Items"])


@router.post("/", response_model=StockOut, status_code=201,
             summary="Tambah bahan baku atau kemasan baru")
def create_stock(data: StockCreate, db: Session = Depends(get_db)):
    return stock_service.create_stock(db, data)


@router.get("/", response_model=list[StockOut],
            summary="List semua stok — bisa filter by kategori (bahan_baku / kemasan)")
def list_stock(
    kategori: Optional[str] = Query(None, description="bahan_baku | kemasan"),
    db: Session = Depends(get_db),
):
    return stock_service.get_all_stock(db, kategori)


@router.get("/{stock_id}", response_model=StockOut)
def get_stock(stock_id: int, db: Session = Depends(get_db)):
    return stock_service.get_stock_or_404(db, stock_id)


@router.put("/{stock_id}", response_model=StockOut,
            summary="Edit data bahan (nama, satuan, kategori, harga, stok)")
def update_stock(stock_id: int, data: StockUpdate, db: Session = Depends(get_db)):
    return stock_service.update_stock(db, stock_id, data)


@router.delete("/{stock_id}", summary="Hapus bahan — gagal jika masih dipakai di resep")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    return stock_service.delete_stock(db, stock_id)