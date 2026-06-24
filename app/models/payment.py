import enum
from decimal import Decimal

from sqlalchemy import Column, Integer, String, Text, Numeric, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class PaymentStatusEnum(str, enum.Enum):
    success = "Success"
    pending = "Pending"
    failed = "Failed"
    refunded = "Refunded"


class PaymentTypeEnum(str, enum.Enum):
    dp = "DP"
    final = "Final"


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    pg_transaction_id = Column(String(100), nullable=True)
    jumlah_bayar = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    payment_status = Column(
        SAEnum(PaymentStatusEnum, name="paymentstatusenum"),
        nullable=False,
        default=PaymentStatusEnum.pending,
    )
    payment_type = Column(
        SAEnum(PaymentTypeEnum, name="paymenttypeenum"),
        nullable=False,
        default=PaymentTypeEnum.final,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    va_number = Column(String(50), nullable=True)
    qris_url = Column(Text, nullable=True)

    # Relationships
    invoice = relationship("Invoice", back_populates="payments")
    verified_by_user = relationship("User", foreign_keys=[verified_by])

    def __repr__(self):
        return f"<Payment(id={self.id}, invoice_id={self.invoice_id}, status={self.payment_status})>"