from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.stock_item import StockItem
from app.schemas.stock import StockCreate, StockUpdate
from typing import Optional


def create(db: Session, data: StockCreate) -> StockItem:
    item = StockItem(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def get_by_id(db: Session, stock_id: int) -> Optional[StockItem]:
    return db.query(StockItem).filter(StockItem.id == stock_id).first()


def get_all(db: Session, kategori: Optional[str] = None) -> list[StockItem]:
    q = db.query(StockItem)
    if kategori:
        q = q.filter(StockItem.kategori == kategori)
    return q.order_by(StockItem.nama_bahan).all()


def update(db: Session, item: StockItem, data: StockUpdate) -> StockItem:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


def delete(db: Session, item: StockItem) -> bool:
    db.delete(item)
    db.commit()
    return True


def update_average_cost(
    db: Session,
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
    item = db.query(StockItem).with_for_update().filter(StockItem.id == stock_id).first()
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

    db.commit()
    db.refresh(item)
    return item