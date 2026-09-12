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
    is_available = Column(Boolean, default=True, nullable=False)
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

    @property
    def name(self) -> str:
        return self.nama_produk
    
    # Relationships
    recipes = relationship("Recipe", back_populates="product", cascade="all, delete-orphan", lazy="selectin")
    price_histories = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan", lazy="selectin")

    @property
    def stock_quantity(self) -> int:
        from sqlalchemy.orm import attributes
        state = attributes.instance_state(self)
        if "recipes" in state.unloaded or not self.recipes:
            return 0
        max_creatable = None
        for r in self.recipes:
            r_state = attributes.instance_state(r)
            if "stock_item" in r_state.unloaded or not r.stock_item:
                return 0
            if not r.jumlah_dibutuhkan or r.jumlah_dibutuhkan <= 0:
                continue
            available_stock = r.stock_item.stok_tersedia or 0
            if available_stock <= 0:
                return 0
            available = int(available_stock // r.jumlah_dibutuhkan)
            if max_creatable is None or available < max_creatable:
                max_creatable = available
        return max(0, max_creatable if max_creatable is not None else 0)

    @property
    def is_in_stock(self) -> bool:
        return bool(self.is_available and self.stock_quantity > 0)
