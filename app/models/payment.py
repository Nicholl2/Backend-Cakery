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


class RefundStatusEnum(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    pg_transaction_id = Column(String(100), nullable=True)
    jumlah_bayar = Column(Numeric(10, 2), nullable=False)
    payment_method = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)
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
    settled_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    va_number = Column(String(50), nullable=True)
    qris_url = Column(Text, nullable=True)

    # Relationships
    invoice = relationship("Invoice", back_populates="payments")
    verified_by_user = relationship("User", foreign_keys=[verified_by])
    refunds = relationship("RefundTransaction", back_populates="payment", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self):
        return f"<Payment(id={self.id}, invoice_id={self.invoice_id}, status={self.payment_status})>"


class RefundTransaction(Base):
    __tablename__ = "refund_transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False, index=True)
    refund_amount = Column(Numeric(10, 2), nullable=False)
    status = Column(SAEnum(RefundStatusEnum, name="refundstatusenum"), nullable=False,
                    default=RefundStatusEnum.pending)
    reason = Column(String(500), nullable=False)
    gateway_ref = Column(String(100), nullable=True, unique=True)
    initiated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    initiated_source = Column(String(32), nullable=False, default="seller")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    payment = relationship("Payment", back_populates="refunds")
    initiator = relationship("User", foreign_keys=[initiated_by])
