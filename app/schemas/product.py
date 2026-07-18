from pydantic import BaseModel, Field, field_validator, model_validator
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from datetime import datetime

def _round2(v) -> Optional[Decimal]:
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

# ── CREATE ──────────────────────────────────────────────────────────────────
class ProductCreate(BaseModel):
    nama_produk: str = Field(..., min_length=1, max_length=100)
    deskripsi: Optional[str] = None
    kategori: Optional[str] = Field(None, max_length=50)
    harga_jual: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    is_active: bool = True
    minimum_order: int = Field(1, ge=1)


# ── UPDATE (Hanya edit deskripsi, harga_jual, is_active, image_url, minimum_order) ──
class ProductUpdate(BaseModel):
    deskripsi: Optional[str] = None
    harga_jual: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    is_active: Optional[bool] = None
    image_url: Optional[str] = None
    minimum_order: Optional[int] = Field(None, ge=1)


# ── SET PRICE (khusus Owner — Use Case 2 / TOTI-02) ─────────────────────────
class SetPriceRequest(BaseModel):
    harga_jual: Decimal = Field(..., gt=0, decimal_places=2)
    changed_by: Optional[str] = Field(None, description="Username/ID Owner yang mengubah harga")


class SetPriceResponse(BaseModel):
    product_id: int
    nama_produk: str
    hpp_total: Decimal
    harga_jual_baru: Decimal
    margin_persen: Optional[float] = None
    warning_below_hpp: bool = Field(
        False,
        description="True jika harga_jual < hpp_total (peringatan ke Owner)"
    )
    @field_validator('hpp_total', 'harga_jual_baru', mode='before')
    @classmethod
    def round_money(cls, v):
        return _round2(v)

# ── OUT ──────────────────────────────────────────────────────────────────────
class ProductOut(BaseModel):
    id: int
    nama_produk: str
    deskripsi: Optional[str] = None
    kategori: Optional[str] = None
    hpp_total: Decimal
    harga_jual: Optional[Decimal] = None
    markup_percentage: Optional[Decimal] = None
    is_active: bool
    is_available: bool
    image_url: Optional[str] = None
    
    # New catalog fields requested by Frontend
    slug: Optional[str] = None
    rating: float = 0.0
    review_count: int = 0
    sold_count: int = 0
    is_featured: bool = False
    minimum_order: int = 1
    parent_category: Optional[str] = None
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode='before')
    @classmethod
    def set_parent_category(cls, data):
        if isinstance(data, dict):
            if not data.get('parent_category') and data.get('kategori'):
                data['parent_category'] = data['kategori']
        return data

    @field_validator('hpp_total', 'harga_jual', mode='before')
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    @field_validator('markup_percentage', mode='before')
    @classmethod
    def round_markup(cls, v):
        if v is None:
            return None
        return Decimal(str(v)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)

    class Config:
        from_attributes = True


# ── PRICING BREAKDOWN ────────────────────────────────────────────────────────
class CostDetail(BaseModel):
    bahan: str
    satuan: str
    qty: float
    unit_price: float
    cost: float
    @field_validator('unit_price', 'cost', mode='before')
    @classmethod
    def round_float(cls, v):
        if v is None:
            return v
        return round(float(v), 2)


class PricingResponse(BaseModel):
    product_id: int
    nama_produk: str
    hpp: Decimal
    harga_jual: Optional[Decimal] = None
    margin_persen: Optional[float] = None
    warning_below_hpp: bool = False
    breakdown: list[CostDetail]
    @field_validator('hpp', 'harga_jual', mode='before')
    @classmethod
    def round_money(cls, v):
        return _round2(v)


# ── PRICE HISTORY OUT ────────────────────────────────────────────────────────
class PriceHistoryOut(BaseModel):
    id: int
    product_id: int
    harga_jual_lama: Optional[Decimal]
    harga_jual_baru: Decimal
    hpp_saat_itu: Decimal
    changed_by: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True