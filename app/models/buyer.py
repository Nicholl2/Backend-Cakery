from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Buyer(Base):
    """Buyer users for standard customer site access"""
    __tablename__ = "buyers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    avatar_url = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False, server_default="false", nullable=False)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    wishlists = relationship("Wishlist", back_populates="buyer", cascade="all, delete-orphan")
    # if there are other relationships like orders, they should be added here too.

    def __repr__(self):
        return f"<Buyer(id={self.id}, name={self.name}, email={self.email})>"

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

class Wishlist(Base):
    __tablename__ = "wishlists"

    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('buyer_id', 'product_id', name='uq_buyer_product'),
    )

    buyer = relationship("Buyer", back_populates="wishlists")
    product = relationship("Product")

