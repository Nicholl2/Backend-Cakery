import enum
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Numeric, ForeignKey,
    DateTime, Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class OrderStatusEnum(str, enum.Enum):
    pending = "pending"
    in_process = "in_process"
    ready = "ready"
    delivered = "delivered"
    picked_up = "picked_up"
    cancelled = "cancelled"


class MetodePengirimanEnum(str, enum.Enum):
    pickup = "pickup"
    delivery = "delivery"


class InvoiceStatusEnum(str, enum.Enum):
    unpaid = "unpaid"
    partial = "partial"
    paid = "paid"
    refunded = "refunded"


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    status = Column(
        SAEnum(OrderStatusEnum, name="orderstatusenum"),
        nullable=False,
        default=OrderStatusEnum.pending,
    )
    metode_pengiriman = Column(
        SAEnum(MetodePengirimanEnum, name="metodepengrimanenum"),
        nullable=False,
    )
    total_harga_pesanan = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    created_via = Column(String(50), nullable=False, default="chatbot")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    customer = relationship("Customer", back_populates="orders")
    order_items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    invoice = relationship("Invoice", back_populates="order", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Order(id={self.id}, status={self.status}, customer_id={self.customer_id})>"


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    jumlah = Column(Integer, nullable=False)
    custom_decoration_charge = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    subtotal = Column(Numeric(10, 2), nullable=False)
    hpp_snapshot = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="order_items")
    product = relationship("Product")

    def __repr__(self):
        return f"<OrderItem(id={self.id}, order_id={self.order_id}, product_id={self.product_id})>"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, unique=True, index=True)
    nomor_invoice = Column(String(30), nullable=False, unique=True, index=True)
    total_tagihan = Column(Numeric(10, 2), nullable=False)
    status = Column(
        SAEnum(InvoiceStatusEnum, name="invoicestatusenum"),
        nullable=False,
        default=InvoiceStatusEnum.unpaid,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    order = relationship("Order", back_populates="invoice")
    payments = relationship("Payment", back_populates="invoice")

    def __repr__(self):
        return f"<Invoice(id={self.id}, nomor_invoice={self.nomor_invoice}, status={self.status})>"