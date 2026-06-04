from pydantic import BaseModel
from decimal import Decimal
from enum import Enum


class SatuanEnum(str, Enum):
    gram = "gram"
    ml = "ml"
    pcs = "pcs"
    kg = "kg"
    liter = "liter"


class StockCreate(BaseModel):
    nama_bahan: str
    satuan: SatuanEnum
    harga_per_satuan: Decimal
    stok_tersedia: Decimal


class StockUpdate(BaseModel):
    nama_bahan: str | None = None
    satuan: SatuanEnum | None = None
    harga_per_satuan: Decimal | None = None
    stok_tersedia: Decimal | None = None


class StockOut(BaseModel):
    id: int
    nama_bahan: str
    satuan: SatuanEnum
    harga_per_satuan: Decimal
    stok_tersedia: Decimal

    class Config:
        from_attributes = True
