from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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

    model_config = ConfigDict(from_attributes=True)


# ── PURCHASE ITEM ───────────────────────────────────────────────────────────

class PurchaseItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_item_id: int = Field(..., ge=1)
    # `jumlah` and `harga_satuan` use the stock item's base unit. New clients
    # should send `harga_total`; the server derives the normalized unit cost.
    jumlah: Decimal = Field(..., gt=0, le=Decimal("999999.9999"), decimal_places=4)
    harga_satuan: Optional[Decimal] = Field(None, gt=0, le=Decimal("999999.9999"), decimal_places=4)
    harga_total: Optional[Decimal] = Field(None, gt=0, le=Decimal("9999999999999.99"), decimal_places=2)
    confirm_cost_deviation: bool = False

    @model_validator(mode="after")
    def require_one_price_input(self):
        if (self.harga_total is None) == (self.harga_satuan is None):
            raise ValueError("Isi tepat satu: harga_total atau harga_satuan.")
        return self


class PurchaseStockOut(BaseModel):
    id: int
    nama_item: str
    base_unit: str = Field(validation_alias="satuan")
    average_cost: Decimal = Field(validation_alias="harga_per_satuan")
    available_quantity: Decimal = Field(validation_alias="stok_tersedia")

    model_config = ConfigDict(from_attributes=True)


class PurchaseItemOut(BaseModel):
    id: int
    purchase_id: int
    stock_item_id: int
    jumlah: Decimal
    harga_satuan: Decimal
    harga_total: Decimal
    cost_deviation_confirmed: bool = False
    stock_item: Optional[PurchaseStockOut] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ── PURCHASE ────────────────────────────────────────────────────────────────

class PurchaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: int = Field(..., ge=1)
    nomor_po: Optional[str] = Field(None, max_length=50)
    catatan: Optional[str] = None
    items: list[PurchaseItemCreate] = Field(..., min_length=1)

    @model_validator(mode="after")
    def unique_stock_items(self):
        ids = [item.stock_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Setiap item inventory hanya boleh muncul satu kali dalam purchase.")
        return self


class PurchaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: Optional[int] = Field(None, ge=1)
    nomor_po: Optional[str] = Field(None, max_length=50)
    catatan: Optional[str] = None
    items: Optional[list[PurchaseItemCreate]] = Field(None, min_length=1)
    is_received: Optional[bool] = None

    @model_validator(mode="after")
    def keep_edit_and_receive_separate(self):
        if "supplier_id" in self.model_fields_set and self.supplier_id is None:
            raise ValueError("supplier_id tidak boleh null saat mengganti supplier.")
        if self.is_received is True and self.items is not None:
            raise ValueError("Simpan perubahan item sebelum menandai purchase diterima.")
        if self.items is not None:
            ids = [item.stock_item_id for item in self.items]
            if len(ids) != len(set(ids)):
                raise ValueError("Setiap item inventory hanya boleh muncul satu kali dalam purchase.")
        return self


class PurchaseCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Alasan pembatalan minimal 3 karakter.")
        return value


class PurchaseOut(BaseModel):
    id: int
    supplier_id: int
    nomor_po: Optional[str] = None
    tanggal_pemesanan: datetime
    tanggal_diterima: Optional[datetime] = None
    total_harga: Decimal
    catatan: Optional[str] = None
    is_received: bool
    is_cancelled: bool = False
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    cancelled_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PurchaseDetailOut(BaseModel):
    id: int
    supplier_id: int
    nomor_po: Optional[str] = None
    tanggal_pemesanan: datetime
    tanggal_diterima: Optional[datetime] = None
    total_harga: Decimal
    catatan: Optional[str] = None
    is_received: bool
    is_cancelled: bool = False
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    cancelled_by: Optional[int] = None
    items: list[PurchaseItemOut] = Field(validation_alias="purchase_items")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
