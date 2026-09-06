from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from app.core.database import Base
from sqlalchemy.orm import relationship


class Customer(Base):
    """Customer data from WhatsApp chatbot interactions"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    nama = Column(String(100), nullable=False)
    nomor_wa = Column(String(20), unique=True, nullable=False, index=True)
    alamat = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    human_takeover_active = Column(Boolean, default=False, nullable=False)
    takeover_expires_at = Column(DateTime(timezone=True), nullable=True)
    orders = relationship("Order", back_populates="customer", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Customer(id={self.id}, nomor_wa={self.nomor_wa}, takeover={self.human_takeover_active})>"