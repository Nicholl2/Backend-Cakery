from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class User(Base):
    """Internal users for system access"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    nomor_wa_admin = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    role = relationship("Role")
    faq_items = relationship("FaqItem", back_populates="created_by_user", foreign_keys="FaqItem.created_by")
    expenses = relationship("Expense", back_populates="recorded_by_user")
    stock_items_updated = relationship("StockItem", back_populates="last_updated_by_user")
    purchase_orders = relationship("Purchase", back_populates="created_by_user")

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role_id={self.role_id})>"
