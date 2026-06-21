from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union
from datetime import datetime


class RecipeCreate(BaseModel):
    stock_item_id: int
    jumlah_dibutuhkan: Decimal = Field(..., gt=0, decimal_places=4,
                                       description="Jumlah bahan yang dibutuhkan per 1 unit produk")


class RecipeUpdate(BaseModel):
    jumlah_dibutuhkan: Decimal = Field(..., gt=0, decimal_places=4)


class RecipeOut(BaseModel):
    id: int
    product_id: int
    stock_item_id: int
    jumlah_dibutuhkan: Union[int, Decimal]

    # Info bahan baku (join)
    nama_bahan: Optional[str] = None
    satuan: Optional[str] = None
    harga_per_satuan: Optional[Decimal] = None

    # Biaya kontribusi bahan ini ke HPP
    biaya_bahan: Optional[Decimal] = None

    created_at: Optional[datetime] = None

    @field_validator('jumlah_dibutuhkan', mode='before')
    @classmethod
    def normalize_qty(cls, v):
        if v is None:
            return v
        d = Decimal(str(v)).normalize()
        if d == d.to_integral_value():
            return int(d)
        return d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    
    @field_validator('harga_per_satuan', 'biaya_bahan', mode='before')
    @classmethod
    def round_money(cls, v):
        if v is None:
            return v
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    class Config:
        from_attributes = True


class RecipeSummary(BaseModel):
    """Ringkasan resep + HPP total untuk satu produk"""
    product_id: int
    nama_produk: str
    hpp_total: Decimal
    bahan: list[RecipeOut]
    @field_validator('hpp_total', mode='before')
    @classmethod
    def round_money(cls, v):
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)