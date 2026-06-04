from sqlalchemy import Column, Integer, String, Numeric, Enum, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


class SatuanEnum(str, enum.Enum):
    gram = "gram"
    ml = "ml"
    pcs = "pcs"
    kg = "kg"
    liter = "liter"


class KategoriEnum(str, enum.Enum):
    bahan_baku = "bahan_baku"
    kemasan = "kemasan"


class StockItem(Base):
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, index=True)
    nama_item = Column(String(100), nullable=False)
    satuan = Column(Enum(SatuanEnum), nullable=False)
    kategori = Column(Enum(KategoriEnum), nullable=False, default=KategoriEnum.bahan_baku)
    harga_per_satuan = Column(Numeric(10, 4), nullable=False)
    stok_tersedia = Column(Numeric(10, 2), nullable=False)
    version = Column(Integer, default=0, nullable=False)
    
    last_updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    recipes = relationship("Recipe", back_populates="stock_item")
    last_updated_by_user = relationship("User", back_populates="stock_items_updated")
