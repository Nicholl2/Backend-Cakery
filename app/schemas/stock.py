from pydantic import BaseModel, Field, field_validator, ConfigDict
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from datetime import datetime
from typing import Optional


class SatuanEnum(str, Enum):
    gram = "gram"
    ml = "ml"
    pcs = "pcs"
    kg = "kg"
    liter = "liter"


class KategoriEnum(str, Enum):
    bahan_baku = "bahan_baku"
    kemasan = "kemasan"


class StockCreate(BaseModel):
    nama_item: str = Field(..., min_length=1, max_length=100)
    satuan: SatuanEnum
    kategori: KategoriEnum = KategoriEnum.bahan_baku
    harga_per_satuan: Decimal = Field(..., ge=0, decimal_places=4)
    stok_tersedia: Decimal = Field(..., ge=0, decimal_places=4)
    alert_min_stok: Optional[Decimal] = Field(Decimal("0.00"), ge=0, decimal_places=4)
    model_config = ConfigDict(extra="forbid")


class StockUpdate(BaseModel):
    nama_item: Optional[str] = Field(None, min_length=1, max_length=100)
    satuan: Optional[SatuanEnum] = None
    kategori: Optional[KategoriEnum] = None
    # Balance and cost change through ledger transactions, never master edits.
    alert_min_stok: Optional[Decimal] = Field(None, ge=0)
    model_config = ConfigDict(extra="forbid")


class StockOut(BaseModel):
    id: int
    nama_item: str
    satuan: SatuanEnum
    kategori: KategoriEnum
    harga_per_satuan: Decimal
    stok_tersedia: Decimal
    alert_min_stok: Decimal
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator('harga_per_satuan', mode='before')
    @classmethod
    def round_money(cls, v):
        if v is None:
            return Decimal("0.00")
        return Decimal(str(v)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    @field_validator('stok_tersedia', mode='before')
    @classmethod
    def round_stock(cls, v):
        if v is None:
            return Decimal("0")
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @field_validator('alert_min_stok', mode='before')
    @classmethod
    def round_alert(cls, v):
        if v is None:
            return Decimal("0.00")
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    model_config = ConfigDict(from_attributes=True)


class StockAdjustmentCreate(BaseModel):
    quantity_difference: Decimal = Field(..., decimal_places=4)
    reason: str = Field(..., min_length=1, max_length=500)
    unit_cost: Decimal = Field(Decimal("0"), ge=0, decimal_places=4)

    @field_validator("quantity_difference")
    @classmethod
    def require_nonzero_delta(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("quantity_difference harus bukan nol")
        return value


class StockMovementOut(BaseModel):
    id: int
    stock_item_id: int
    movement_type: str
    quantity: Decimal
    unit_cost: Decimal
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    created_by: Optional[int] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
