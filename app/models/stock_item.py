from sqlalchemy import Column, Integer, String, Numeric, Enum, DateTime
from sqlalchemy.sql import func
from app.core.database import Base
import enum


class SatuanEnum(str, enum.Enum):
    gram = "gram"
    ml = "ml"
    pcs = "pcs"
    kg = "kg"
    liter = "liter"


class StockItem(Base):
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    nama_bahan = Column(String(100), nullable=False)
    satuan = Column(Enum(SatuanEnum), nullable=False)
    harga_per_satuan = Column(Numeric(10, 2), nullable=False)
    stok_tersedia = Column(Numeric(10, 2), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
