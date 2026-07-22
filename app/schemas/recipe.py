from pydantic import BaseModel, Field, field_validator, model_validator
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union
from datetime import datetime
from app.schemas.stock import StockOut


class RecipeCreate(BaseModel):
    stock_item_id: int
    jumlah_dibutuhkan: Optional[Decimal] = Field(None, gt=0, decimal_places=4,
                                                 description="Jumlah bahan yang dibutuhkan per 1 unit produk")
    quantity_required: Optional[Decimal] = Field(None, gt=0, decimal_places=4,
                                                 description="Jumlah bahan yang dibutuhkan per 1 unit produk")
    unit: Optional[str] = Field(None, description="Satuan bahan baku")

    @model_validator(mode='before')
    @classmethod
    def sync_quantities(cls, data):
        if isinstance(data, dict):
            qty = data.get('quantity_required') or data.get('jumlah_dibutuhkan')
            if qty is not None:
                data['quantity_required'] = qty
                data['jumlah_dibutuhkan'] = qty
        return data


class RecipeUpdate(BaseModel):
    jumlah_dibutuhkan: Optional[Decimal] = Field(None, gt=0, decimal_places=4)
    quantity_required: Optional[Decimal] = Field(None, gt=0, decimal_places=4)
    unit: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def sync_quantities(cls, data):
        if isinstance(data, dict):
            qty = data.get('quantity_required') or data.get('jumlah_dibutuhkan')
            if qty is not None:
                data['quantity_required'] = qty
                data['jumlah_dibutuhkan'] = qty
        return data


class RecipeOut(BaseModel):
    id: int
    product_id: int
    stock_item_id: int
    jumlah_dibutuhkan: Union[int, Decimal]
    quantity_required: Optional[Union[int, Decimal]] = None
    unit: Optional[str] = None

    # Optional nested stock item
    stock_item: Optional[StockOut] = None

    # Info bahan baku (join)
    nama_bahan: Optional[str] = None
    satuan: Optional[str] = None
    harga_per_satuan: Optional[Decimal] = None

    # Biaya kontribusi bahan ini ke HPP
    biaya_bahan: Optional[Decimal] = None

    created_at: Optional[datetime] = None

    @model_validator(mode='before')
    @classmethod
    def populate_from_stock_item(cls, data):
        if isinstance(data, dict):
            # Try to populate from dict stock_item or attributes
            stock_item = data.get('stock_item')
            if stock_item:
                if isinstance(stock_item, dict):
                    data['nama_bahan'] = stock_item.get('nama_item')
                    data['satuan'] = stock_item.get('satuan')
                    data['harga_per_satuan'] = stock_item.get('harga_per_satuan')
                else:
                    data['nama_bahan'] = getattr(stock_item, 'nama_item', None)
                    data['satuan'] = getattr(stock_item, 'satuan', None)
                    data['harga_per_satuan'] = getattr(stock_item, 'harga_per_satuan', None)
            
            # Sync quantity_required / jumlah_dibutuhkan
            qty = data.get('quantity_required') or data.get('jumlah_dibutuhkan')
            if qty is not None:
                data['quantity_required'] = qty
                data['jumlah_dibutuhkan'] = qty
                
            # Populate unit from stock_item.satuan if not present
            if not data.get('unit'):
                if data.get('satuan'):
                    data['unit'] = data.get('satuan')
                elif stock_item:
                    data['unit'] = getattr(stock_item, 'satuan', None)

            # Calculate biaya_bahan if possible
            if data.get('jumlah_dibutuhkan') is not None and data.get('harga_per_satuan') is not None:
                data['biaya_bahan'] = Decimal(str(data['jumlah_dibutuhkan'])) * Decimal(str(data['harga_per_satuan']))
        else:
            # Handle object instances
            stock_item = getattr(data, 'stock_item', None)
            if stock_item:
                data.nama_bahan = getattr(stock_item, 'nama_item', None)
                data.satuan = getattr(stock_item, 'satuan', None)
                data.harga_per_satuan = getattr(stock_item, 'harga_per_satuan', None)
            
            qty = getattr(data, 'quantity_required', None) or getattr(data, 'jumlah_dibutuhkan', None)
            if qty is not None:
                data.quantity_required = qty
                data.jumlah_dibutuhkan = qty
                
            if not getattr(data, 'unit', None):
                if getattr(data, 'satuan', None):
                    data.unit = getattr(data, 'satuan', None)
                elif stock_item:
                    data.unit = getattr(stock_item, 'satuan', None)

            if getattr(data, 'jumlah_dibutuhkan', None) is not None and getattr(data, 'harga_per_satuan', None) is not None:
                data.biaya_bahan = Decimal(str(data.jumlah_dibutuhkan)) * Decimal(str(data.harga_per_satuan))
        return data

    @field_validator('jumlah_dibutuhkan', 'quantity_required', mode='before')
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