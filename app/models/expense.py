from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
from decimal import Decimal


class Expense(Base):
    """Operational expenses for P&L tracking"""
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    kategori = Column(String(50), nullable=False)  # 'electricity', 'salary', 'rent', etc.
    jumlah = Column(Numeric(15, 2), nullable=False)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    tanggal = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    recorded_by_user = relationship("User", back_populates="expenses")

    def __repr__(self):
        return f"<Expense(id={self.id}, kategori={self.kategori}, jumlah={self.jumlah})>"