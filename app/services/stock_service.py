from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.stock_item import StockItem
from app.schemas.stock import StockCreate, StockUpdate
from app.repositories import stock_repo
from typing import Optional


def create_stock(db: Session, data: StockCreate) -> StockItem:
    # Cek duplikat nama
    existing = db.query(StockItem).filter(StockItem.nama_item == data.nama_item).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item '{data.nama_item}' sudah terdaftar di database.",
        )
    return stock_repo.create(db, data)


def get_all_stock(db: Session, kategori: Optional[str] = None) -> list[StockItem]:
    return stock_repo.get_all(db, kategori)


def get_stock_or_404(db: Session, stock_id: int) -> StockItem:
    item = stock_repo.get_by_id(db, stock_id)
    if not item:
        raise HTTPException(status_code=404, detail="Stock item tidak ditemukan.")
    return item


def update_stock(db: Session, stock_id: int, data: StockUpdate) -> StockItem:
    item = get_stock_or_404(db, stock_id)
    return stock_repo.update(db, item, data)


def delete_stock(db: Session, stock_id: int) -> bool:
    item = get_stock_or_404(db, stock_id)

    # Cegah hapus bahan yang masih dipakai di resep
    if item.recipes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Item '{item.nama_item}' masih digunakan dalam {len(item.recipes)} resep. "
                "Hapus resep yang menggunakannya terlebih dahulu."
            ),
        )
    return stock_repo.delete(db, item)