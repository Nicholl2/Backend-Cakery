from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from typing import Optional
from app.core.database import Base


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", "customer_id", name="uq_review_order_product_customer"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    komentar = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    order = relationship("Order", lazy="selectin")
    product = relationship("Product", lazy="selectin")
    customer = relationship("Customer", lazy="selectin")

    @property
    def comment(self) -> Optional[str]:
        return self.komentar

    @comment.setter
    def comment(self, value: Optional[str]):
        self.komentar = value

    @property
    def product_name(self) -> Optional[str]:
        if self.product:
            return getattr(self.product, "nama_produk", None) or getattr(self.product, "name", None)
        return None

    @property
    def customer_name(self) -> Optional[str]:
        if self.customer:
            return getattr(self.customer, "nama", None)
        return None

    def __repr__(self):
        return f"<Review(id={self.id}, order_id={self.order_id}, product_id={self.product_id}, rating={self.rating})>"
