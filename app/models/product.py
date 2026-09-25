from decimal import Decimal
from typing import Optional
from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    products = relationship("Product", back_populates="category_rel")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    nama_produk = Column(String(100), nullable=False)
    deskripsi = Column(Text, nullable=True)
    kategori = Column(String(50), nullable=True)
    harga_jual = Column(Numeric(10, 2), nullable=True)
    hpp_total = Column(Numeric(10, 2), nullable=True, default=0)
    markup_percentage = Column(Numeric(5, 4), nullable=True)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    is_available = Column(Boolean, default=True, server_default="true", nullable=False)
    image_url = Column(Text, nullable=True)
    
    # New catalog fields requested by Frontend
    slug = Column(String(100), unique=True, index=True, nullable=True)
    rating = Column(Numeric(3, 2), default=Decimal("0.00"), nullable=False)
    review_count = Column(Integer, default=0, nullable=False)
    sold_count = Column(Integer, default=0, nullable=False)
    is_featured = Column(Boolean, default=False, server_default="false", nullable=False)
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
    category_rel = relationship("Category", back_populates="products")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan", lazy="selectin", order_by="ProductImage.id")
    recipes = relationship("Recipe", back_populates="product", cascade="all, delete-orphan", lazy="selectin")
    price_histories = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan", lazy="selectin")

    @property
    def primary_image_url(self) -> Optional[str]:
        if hasattr(self, "images") and self.images:
            for img in self.images:
                if img.is_primary:
                    return img.image_url
            return self.images[0].image_url
        return self.image_url

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


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    image_url = Column(Text, nullable=False)
    is_primary = Column(Boolean, default=False, server_default="false", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    product = relationship("Product", back_populates="images")

