from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class OTPCode(Base):
    """OTP code records for buyers and sellers verification"""
    __tablename__ = "otp_codes"

    id = Column(String(36), primary_key=True)  # UUID representation
    target = Column(String(100), nullable=False, index=True)
    channel = Column(String(50), nullable=False)  # whatsapp, email
    purpose = Column(String(50), nullable=False)  # register, login, reset_password
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<OTPCode(id={self.id}, target={self.target}, purpose={self.purpose}, is_used={self.is_used})>"
