from sqlalchemy import Column, Integer, ForeignKey, Numeric, DateTime, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class PriceHistory(Base):
    __tablename__ = "price_histories"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    harga_jual_lama = Column(Numeric(10, 2), nullable=True)
    harga_jual_baru = Column(Numeric(10, 2), nullable=False)
    hpp_saat_itu = Column(Numeric(10, 2), nullable=False)
    changed_by = Column(String(100), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    product = relationship("Product", back_populates="price_histories")
