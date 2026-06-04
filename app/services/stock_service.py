from sqlalchemy.orm import Session
from app.models.stock_item import StockItem
from app.schemas.stock import StockCreate, StockUpdate


def create_stock(db: Session, data: StockCreate):
    obj = StockItem(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_all_stock(db: Session):
    return db.query(StockItem).all()


def get_stock(db: Session, stock_id: int):
    return db.query(StockItem).filter(StockItem.id == stock_id).first()


def update_stock(db: Session, stock_id: int, data: StockUpdate):
    obj = get_stock(db, stock_id)
    if not obj:
        return None

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    db.commit()
    db.refresh(obj)
    return obj


def delete_stock(db: Session, stock_id: int):
    obj = get_stock(db, stock_id)
    if not obj:
        return False

    db.delete(obj)
    db.commit()
    return True
