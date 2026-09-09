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
    notes: Optional[str] = None
    due_date: Optional[datetime] = None
    payment_method_preference: Optional[str] = None


class BuyerOrderCreate(BaseModel):
    metode_pengiriman: str = Field(..., pattern="^(pickup|delivery)$")
    items: list[OrderItemCreate] = Field(..., min_length=1)
    created_via: str = "web"
    notes: Optional[str] = None
    due_date: Optional[datetime] = None
    payment_method_preference: Optional[str] = None


class CustomOrderItemCreate(BaseModel):
    custom_product_name: str = Field(..., min_length=1)
    price: Decimal = Field(..., gt=0, decimal_places=2)
    qty: int = Field(default=1, gt=0)
    custom_decoration_charge: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)


class CustomOrderCreate(BaseModel):
    customer_name: str = Field(..., min_length=1)
    customer_phone: str = Field(..., min_length=5)
    customer_address: Optional[str] = None
    metode_pengiriman: str = Field(default="pickup", pattern="^(pickup|delivery)$")
    notes: Optional[str] = None
    due_date: Optional[datetime] = None
    payment_method_preference: Optional[str] = None
    items: list[CustomOrderItemCreate] = Field(..., min_length=1)


# ── OUTPUT ───────────────────────────────────────────────────────────────────

class CustomerOrderOut(BaseModel):
    id: int
    nama: str
    nomor_wa: str
    alamat: Optional[str] = None

    class Config:
        from_attributes = True


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
    product_id: Optional[int] = None
    custom_product_name: Optional[str] = None
    jumlah: int
    custom_decoration_charge: Decimal = Decimal("0.00")
    subtotal: Decimal
    hpp_snapshot: Decimal = Decimal("0.00")

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
    notes: Optional[str] = None
    due_date: Optional[datetime] = None
    payment_method_preference: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    customer: Optional[CustomerOrderOut] = None
    order_items: list[OrderItemOut] = Field(default_factory=list)
    items: Optional[list[OrderItemOut]] = Field(default=None, validation_alias="order_items")
    invoice: Optional[InvoiceOut] = None
    amount_paid: Optional[Decimal] = None
    amount_due: Optional[Decimal] = None

    @field_validator("total_harga_pesanan", "amount_paid", "amount_due", mode="before")
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    class Config:
        from_attributes = True
        populate_by_name = True


from app.models.order import OrderStatusEnum

class OrderStatusUpdate(BaseModel):
    status: OrderStatusEnum