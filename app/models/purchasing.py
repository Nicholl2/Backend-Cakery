from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, DateTime, Text, Boolean, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    nama_supplier = Column(String(100), nullable=False, unique=True, index=True)
    kontak_person = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    nomor_telepon = Column(String(20), nullable=True)
    alamat = Column(Text, nullable=True)
    kota = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    purchases = relationship("Purchase", back_populates="supplier")


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("NOT (is_received AND is_cancelled)", name="ck_purchases_not_received_and_cancelled"),
    )

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    nomor_po = Column(String(50), unique=True, nullable=True, index=True)
    tanggal_pemesanan = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    tanggal_diterima = Column(DateTime(timezone=True), nullable=True)
    
    total_harga = Column(Numeric(15, 2), nullable=False, default=0)
    catatan = Column(Text, nullable=True)
    is_received = Column(Boolean, default=False, server_default="false", nullable=False)
    is_cancelled = Column(Boolean, default=False, server_default="false", nullable=False)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    cancelled_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    supplier = relationship("Supplier", back_populates="purchases")
    created_by_user = relationship("User", back_populates="purchase_orders", foreign_keys=[created_by])
    purchase_items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    stock_item_id = Column(Integer, ForeignKey("stock_items.id"), nullable=False)
    
    jumlah = Column(Numeric(10, 4), nullable=False)
    harga_satuan = Column(Numeric(10, 4), nullable=False)
    harga_total = Column(Numeric(15, 2), nullable=False)
    cost_deviation_confirmed = Column(Boolean, nullable=False, default=False, server_default="false")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    purchase = relationship("Purchase", back_populates="purchase_items")
    stock_item = relationship("StockItem")
