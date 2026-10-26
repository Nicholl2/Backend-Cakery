from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class EmailSenderSettings(Base):
    """Non-secret sender identity editable by the store owner."""
    __tablename__ = "email_sender_settings"

    id = Column(Integer, primary_key=True)
    from_email = Column(String(255), nullable=False)
    from_name = Column(String(100), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
