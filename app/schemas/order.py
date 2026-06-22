from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from datetime import datetime


def _round2(v) -> Optional[Decimal]:
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── INPUT ────────────────────────────────────────────────────────────────────

class OrderItemCreate(BaseModel):
    product_id: int
    jumlah: int = Field(..., gt=0)
    custom_decoration_charge: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)


class OrderCreate(BaseModel):
    customer_id: int
    metode_pengiriman: str = Field(..., pattern="^(pickup|delivery)$")
    items: list[OrderItemCreate] = Field(..., min_length=1)
    created_via: str = "chatbot"


# ── OUTPUT ───────────────────────────────────────────────────────────────────

class InvoiceOut(BaseModel):
    id: int
    nomor_invoice: str
    total_tagihan: Decimal
    status: str
    created_at: Optional[datetime] = None

    @field_validator("total_tagihan", mode="before")
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    class Config:
        from_attributes = True


class OrderItemOut(BaseModel):
    id: int
    product_id: int
    jumlah: int
    custom_decoration_charge: Decimal
    subtotal: Decimal
    hpp_snapshot: Decimal

    @field_validator("custom_decoration_charge", "subtotal", "hpp_snapshot", mode="before")
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    customer_id: int
    status: str
    metode_pengiriman: str
    total_harga_pesanan: Decimal
    created_via: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: list[OrderItemOut] = Field(validation_alias="order_items")
    invoice: Optional[InvoiceOut] = None

    @field_validator("total_harga_pesanan", mode="before")
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    class Config:
        from_attributes = True
        populate_by_name = True