from pydantic import BaseModel, Field, field_validator
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


class StockUpdate(BaseModel):
    nama_item: Optional[str] = Field(None, min_length=1, max_length=100)
    satuan: Optional[SatuanEnum] = None
    kategori: Optional[KategoriEnum] = None
    harga_per_satuan: Optional[Decimal] = Field(None, ge=0)
    stok_tersedia: Optional[Decimal] = Field(None, ge=0)


class StockOut(BaseModel):
    id: int
    nama_item: str
    satuan: SatuanEnum
    kategori: KategoriEnum
    harga_per_satuan: Decimal
    stok_tersedia: Decimal
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    @field_validator('harga_per_satuan', mode='before')
    @classmethod
    def round_money(cls, v):
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    @field_validator('stok_tersedia', mode='before')
    @classmethod
    def round_stock(cls, v):
        return Decimal(str(v)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    class Config:
        from_attributes = True