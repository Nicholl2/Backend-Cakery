from typing import Optional
from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Float
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
    
    # New catalog fields requested by Frontend
    slug = Column(String(100), unique=True, index=True, nullable=True)
    rating = Column(Float, default=0.0, nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    sold_count = Column(Integer, default=0, nullable=False)
    is_featured = Column(Boolean, default=False, nullable=False)
    minimum_order = Column(Integer, default=1, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    @property
    def parent_category(self) -> Optional[str]:
        return self.kategori
    
    # Relationships
    recipes = relationship("Recipe", back_populates="product", cascade="all, delete-orphan")
    price_histories = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan")

    @property
    def is_available(self) -> bool:
        from sqlalchemy.orm import attributes
        state = attributes.instance_state(self)
        if "recipes" in state.unloaded:
            return True
        if not self.recipes:
            return True
        for r in self.recipes:
            r_state = attributes.instance_state(r)
            if "stock_item" in r_state.unloaded:
                continue
            if r.stock_item and r.stock_item.stok_tersedia < r.jumlah_dibutuhkan:
                return False
        return True
