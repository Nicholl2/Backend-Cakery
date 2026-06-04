from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from datetime import datetime


# ── SUPPLIER ────────────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    nama_supplier: str = Field(..., min_length=1, max_length=100)
    kontak_person: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = None
    nomor_telepon: Optional[str] = None
    alamat: Optional[str] = None
    kota: Optional[str] = None


class SupplierUpdate(BaseModel):
    nama_supplier: Optional[str] = Field(None, min_length=1, max_length=100)
    kontak_person: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = None
    nomor_telepon: Optional[str] = None
    alamat: Optional[str] = None
    kota: Optional[str] = None
    is_active: Optional[bool] = None


class SupplierOut(BaseModel):
    id: int
    nama_supplier: str
    kontak_person: Optional[str] = None
    email: Optional[str] = None
    nomor_telepon: Optional[str] = None
    alamat: Optional[str] = None
    kota: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── PURCHASE ITEM ───────────────────────────────────────────────────────────

class PurchaseItemCreate(BaseModel):
    stock_item_id: int
    jumlah: Decimal = Field(..., gt=0, decimal_places=4)
    harga_satuan: Decimal = Field(..., gt=0, decimal_places=4)


class PurchaseItemOut(BaseModel):
    id: int
    purchase_id: int
    stock_item_id: int
    jumlah: Decimal
    harga_satuan: Decimal
    harga_total: Decimal
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── PURCHASE ────────────────────────────────────────────────────────────────

class PurchaseCreate(BaseModel):
    supplier_id: int
    nomor_po: Optional[str] = None
    catatan: Optional[str] = None
    items: list[PurchaseItemCreate] = Field(..., min_items=1)


class PurchaseUpdate(BaseModel):
    nomor_po: Optional[str] = None
    tanggal_diterima: Optional[datetime] = None
    catatan: Optional[str] = None
    is_received: Optional[bool] = None


class PurchaseOut(BaseModel):
    id: int
    supplier_id: int
    nomor_po: Optional[str] = None
    tanggal_pemesanan: datetime
    tanggal_diterima: Optional[datetime] = None
    total_harga: Decimal
    catatan: Optional[str] = None
    is_received: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PurchaseDetailOut(BaseModel):
    id: int
    supplier_id: int
    nomor_po: Optional[str] = None
    tanggal_pemesanan: datetime
    tanggal_diterima: Optional[datetime] = None
    total_harga: Decimal
    catatan: Optional[str] = None
    is_received: bool
    items: list[PurchaseItemOut] = Field(validation_alias="purchase_items")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
