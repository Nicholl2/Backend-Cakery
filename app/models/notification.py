from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Notification(Base):
    """Notification record for persistent in-app notifications (Buyer and Internal Seller Users)"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    recipient_buyer_id = Column(Integer, ForeignKey("buyers.id", ondelete="CASCADE"), nullable=True, index=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    type = Column(String(50), nullable=False, index=True)  # e.g., 'order_created', 'order_status_updated'
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True)

    actor_type = Column(String(20), nullable=True)  # 'buyer', 'user', 'system'
    actor_id = Column(Integer, nullable=True)

    metadata_json = Column(Text, nullable=True)  # JSON string containing order_number, status, etc.
    read_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # Relationships
    buyer = relationship("Buyer", foreign_keys=[recipient_buyer_id])
    user = relationship("User", foreign_keys=[recipient_user_id])
    order = relationship("Order", foreign_keys=[order_id])

    def __repr__(self):
        return f"<Notification(id={self.id}, type={self.type}, buyer_id={self.recipient_buyer_id}, user_id={self.recipient_user_id})>"
