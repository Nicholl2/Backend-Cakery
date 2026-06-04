from sqlalchemy import Column, Integer, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class FaqItem(Base):
    """FAQ items for customer support"""
    __tablename__ = "faq_items"

    id = Column(Integer, primary_key=True, index=True)
    pertanyaan = Column(Text, nullable=False)
    jawaban = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    created_by_user = relationship("User", back_populates="faq_items", foreign_keys=[created_by])

    def __repr__(self):
        return f"<FaqItem(id={self.id}, is_active={self.is_active})>"