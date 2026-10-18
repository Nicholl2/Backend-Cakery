from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Boolean, Text, JSON
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
    deskripsi = Column(String(500), nullable=True)
    penerima = Column(String(120), nullable=True)
    metode_pembayaran = Column(String(20), nullable=True)
    bukti_url = Column(String(2048), nullable=True)
    is_voided = Column(Boolean, nullable=False, default=False, server_default="false")
    void_reason = Column(String(500), nullable=True)
    voided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    recorded_by_user = relationship("User", back_populates="expenses", foreign_keys=[recorded_by])
    audit_events = relationship("ExpenseAudit", back_populates="expense", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Expense(id={self.id}, kategori={self.kategori}, jumlah={self.jumlah})>"


class ExpenseAudit(Base):
    """Append-only audit record for expense creation, edits, and voids."""
    __tablename__ = "expense_audits"

    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(20), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changes = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    expense = relationship("Expense", back_populates="audit_events")
