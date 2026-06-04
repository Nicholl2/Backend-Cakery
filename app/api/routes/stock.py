from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.stock import StockCreate, StockUpdate, StockOut
from app.services import stock_service

router = APIRouter(prefix="/stock", tags=["stock"])


@router.post("/", response_model=StockOut)
def create_stock(data: StockCreate, db: Session = Depends(get_db)):
    return stock_service.create_stock(db, data)


@router.get("/", response_model=list[StockOut])
def list_stock(db: Session = Depends(get_db)):
    return stock_service.get_all_stock(db)


@router.get("/{stock_id}", response_model=StockOut)
def get_stock(stock_id: int, db: Session = Depends(get_db)):
    obj = stock_service.get_stock(db, stock_id)
    if not obj:
        raise HTTPException(404, "Stock not found")
    return obj


@router.put("/{stock_id}", response_model=StockOut)
def update_stock(stock_id: int, data: StockUpdate, db: Session = Depends(get_db)):
    obj = stock_service.update_stock(db, stock_id, data)
    if not obj:
        raise HTTPException(404, "Stock not found")
    return obj


@router.delete("/{stock_id}")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    ok = stock_service.delete_stock(db, stock_id)
    if not ok:
        raise HTTPException(404, "Stock not found")
    return {"deleted": True}
