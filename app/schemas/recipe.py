from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
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
    jumlah_dibutuhkan: Decimal

    # Info bahan baku (join)
    nama_bahan: Optional[str] = None
    satuan: Optional[str] = None
    harga_per_satuan: Optional[Decimal] = None

    # Biaya kontribusi bahan ini ke HPP
    biaya_bahan: Optional[Decimal] = None

    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RecipeSummary(BaseModel):
    """Ringkasan resep + HPP total untuk satu produk"""
    product_id: int
    nama_produk: str
    hpp_total: Decimal
    bahan: list[RecipeOut]