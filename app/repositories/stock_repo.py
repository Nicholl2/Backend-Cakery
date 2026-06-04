from sqlalchemy.orm import Session
from app.models.stock_item import StockItem


def create_stock(db: Session, data):
    item = StockItem(**data.dict())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_stock(db: Session):
    return db.query(StockItem).all()
