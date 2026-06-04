from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    nama_produk = Column(String(100), nullable=False)
    deskripsi = Column(String(500), nullable=True)
    kategori = Column(String(50), nullable=True)
    harga_jual = Column(Numeric(10, 2), nullable=True)
    hpp_total = Column(Numeric(10, 2), nullable=True, default=0)
    markup_percentage = Column(Numeric(5, 4), nullable=True)
    is_active = Column(Boolean, default=True)
    image_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    recipes = relationship("Recipe", back_populates="product", cascade="all, delete-orphan")
    price_histories = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan")
