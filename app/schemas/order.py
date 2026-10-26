from pydantic import BaseModel, Field, field_validator, ConfigDict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from datetime import datetime
import hashlib
import hmac
import re
from pydantic import ValidationInfo
from app.utils.sanitize import sanitize_text


def _round2(v, default: Optional[Decimal] = None) -> Optional[Decimal]:
    if v is None:
        return default
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
    notes: Optional[str] = Field(None, max_length=500)
    due_date: Optional[datetime] = None
    fulfillment_date: datetime
    payment_method_preference: Optional[str] = Field(None, max_length=50)

    @field_validator("fulfillment_date", mode="after")
    @classmethod
    def validate_fulfillment_date(cls, v):
        from app.utils.business_date import validate_fulfillment_date as _validate
        return _validate(v)

    @field_validator("notes", mode="before")
    @classmethod
    def sanitize_notes(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class BuyerOrderCreate(BaseModel):
    metode_pengiriman: str = Field(..., pattern="^(pickup|delivery)$")
    items: list[OrderItemCreate] = Field(..., min_length=1)
    created_via: str = "web"
    notes: Optional[str] = Field(None, max_length=500)
    due_date: Optional[datetime] = None
    fulfillment_date: datetime
    payment_method_preference: Optional[str] = Field(None, max_length=50)

    @field_validator("fulfillment_date", mode="after")
    @classmethod
    def validate_fulfillment_date(cls, v):
        from app.utils.business_date import validate_fulfillment_date as _validate
        return _validate(v)

    @field_validator("notes", mode="before")
    @classmethod
    def sanitize_notes(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class BuyerOrderCancelResponse(BaseModel):
    status: str
    message: str


class CustomOrderItemCreate(BaseModel):
    product_id: int = Field(..., gt=0)
    qty: int = Field(default=1, gt=0)
    custom_decoration_charge: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)


class CustomOrderAccessoryCreate(BaseModel):
    stock_item_id: int = Field(..., gt=0)
    quantity: Decimal = Field(..., gt=0, decimal_places=4)


class CustomOrderCreate(BaseModel):
    customer_name: str = Field(..., min_length=1, max_length=100)
    customer_phone: str = Field(..., min_length=10, max_length=16)
    customer_address: Optional[str] = Field(None, max_length=500)
    metode_pengiriman: str = Field(default="pickup", pattern="^(pickup|delivery)$")
    notes: Optional[str] = Field(None, max_length=500)
    due_date: Optional[datetime] = None
    fulfillment_date: datetime
    payment_method_preference: Optional[str] = Field(None, max_length=50)
    items: list[CustomOrderItemCreate] = Field(..., min_length=1)
    accessories: list[CustomOrderAccessoryCreate] = Field(default_factory=list)

    @field_validator("customer_name", mode="after")
    @classmethod
    def sanitize_name(cls, v):
        return sanitize_text(v)

    @field_validator("fulfillment_date", mode="after")
    @classmethod
    def validate_fulfillment_date(cls, v):
        from app.utils.business_date import validate_fulfillment_date as _validate
        return _validate(v)

    @field_validator("notes", "customer_address", mode="before")
    @classmethod
    def sanitize_text_fields(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class RefundRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500, description="Alasan melakukan refund")
    amount: Optional[Decimal] = Field(None, gt=0, decimal_places=2, description="Nominal refund; kosong berarti seluruh saldo refundable")
    nomor_wa: Optional[str] = Field(None, max_length=20, description="Nomor WA customer pemesan (wajib diisi jika dipanggil via Chatbot Service Key)")

    @field_validator("reason", mode="before")
    @classmethod
    def sanitize_reason(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class RefundResponse(BaseModel):
    message: str = "Order refund processed successfully"
    order_id: int
    status: str
    payment_status: str
    refund_mode: str  # "auto" | "manual" | "in_progress"

    model_config = ConfigDict(from_attributes=True)

    def __getitem__(self, item):
        return getattr(self, item)


# ── OUTPUT ───────────────────────────────────────────────────────────────────

class CustomerOrderOut(BaseModel):
    id: int
    nama: Optional[str] = "Customer"
    nomor_wa: Optional[str] = ""
    alamat: Optional[str] = None

    @field_validator("nama", mode="before")
    @classmethod
    def fallback_nama(cls, v):
        if not v:
            return "Customer"
        return str(v)

    @field_validator("nomor_wa", mode="before")
    @classmethod
    def fallback_nomor_wa(cls, v):
        if not v:
            return ""
        return str(v)

    model_config = ConfigDict(from_attributes=True)


class InvoiceOut(BaseModel):
    id: int
    nomor_invoice: str
    total_tagihan: Optional[Decimal] = Decimal("0.00")
    status: str
    created_at: Optional[datetime] = None

    @field_validator("total_tagihan", mode="before")
    @classmethod
    def round_money(cls, v):
        if v is None:
            return Decimal("0.00")
        return _round2(v, default=Decimal("0.00"))

    @field_validator("status", mode="before")
    @classmethod
    def fallback_status(cls, v):
        if not v:
            return "unpaid"
        if hasattr(v, "value"):
            return v.value
        return str(v)

    model_config = ConfigDict(from_attributes=True)


class OrderItemOut(BaseModel):
    id: int
    product_id: Optional[int] = None
    custom_product_name: Optional[str] = None
    product_name: Optional[str] = None
    unit_price_snapshot: Optional[Decimal] = None
    jumlah: int = 1
    custom_decoration_charge: Optional[Decimal] = Decimal("0.00")
    subtotal: Optional[Decimal] = Decimal("0.00")
    hpp_snapshot: Optional[Decimal] = Decimal("0.00")

    @field_validator("jumlah", mode="before")
    @classmethod
    def fallback_jumlah(cls, v):
        if v is None:
            return 1
        return int(v)

    @field_validator("custom_decoration_charge", "hpp_snapshot", mode="before")
    @classmethod
    def sanitize_null_floats(cls, v):
        return 0.0 if v is None else v

    @field_validator("subtotal", mode="before")
    @classmethod
    def round_subtotal(cls, v):
        if v is None:
            return Decimal("0.00")
        return _round2(v, default=Decimal("0.00"))

    model_config = ConfigDict(from_attributes=True)


class OrderAccessoryOut(BaseModel):
    id: int
    stock_item_id: int
    stock_item_name: str
    unit: str
    quantity: Decimal
    unit_cost_snapshot: Decimal
    subtotal: Decimal

    model_config = ConfigDict(from_attributes=True)


# Aliases for compatibility
OrderItemRead = OrderItemOut
OrderItemResponse = OrderItemOut



class OrderOut(BaseModel):
    id: int
    customer_id: int
    status: str
    metode_pengiriman: str = "pickup"
    total_harga_pesanan: Optional[Decimal] = Decimal("0.00")
    created_via: str = "BUYER_SITE"
    notes: Optional[str] = None
    due_date: Optional[datetime] = None
    fulfillment_date: Optional[datetime] = None
    settlement_due_date: Optional[datetime] = None
    payment_expires_at: Optional[datetime] = None
    fulfillment_overdue_notified_at: Optional[datetime] = None
    payment_method_preference: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    customer: Optional[CustomerOrderOut] = None
    order_items: list[OrderItemOut] = Field(default_factory=list)
    items: Optional[list[OrderItemOut]] = Field(default=None, validation_alias="order_items")
    accessories: list[OrderAccessoryOut] = Field(default_factory=list)
    invoice: Optional[InvoiceOut] = None
    amount_paid: Optional[Decimal] = None
    amount_due: Optional[Decimal] = None
    payment_status: Optional[str] = None

    @field_validator("fulfillment_date", "settlement_due_date", "payment_expires_at", "fulfillment_overdue_notified_at", "due_date", "created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc_timezone(cls, v):
        from datetime import timezone as tz
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=tz.utc)
        return v

    @field_validator("created_via", mode="before")
    @classmethod
    def sanitize_created_via(cls, v):
        return "BUYER_SITE" if not v else v

    @field_validator("metode_pengiriman", mode="before")
    @classmethod
    def fallback_metode(cls, v):
        if not v:
            return "pickup"
        if hasattr(v, "value"):
            return v.value
        return str(v)

    @field_validator("status", mode="before")
    @classmethod
    def fallback_status(cls, v):
        if not v:
            return "pending"
        if hasattr(v, "value"):
            return v.value
        return str(v)

    @field_validator("total_harga_pesanan", mode="before")
    @classmethod
    def round_total(cls, v):
        if v is None:
            return Decimal("0.00")
        return _round2(v, default=Decimal("0.00"))

    @field_validator("amount_paid", "amount_due", mode="before")
    @classmethod
    def round_amounts(cls, v):
        return _round2(v)

    @field_validator("notes", mode="before")
    @classmethod
    def hide_order_idempotency_marker(cls, v, info: ValidationInfo):
        if isinstance(v, str):
            marker_v2 = re.search(
                r"\x1eorder-idempotency-v2:([a-zA-Z0-9:_-]+):([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64})\x1e$",
                v,
            )
            marker_v1 = re.search(
                r"\x1eorder-idempotency-v1:([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64})\x1e$",
                v,
            )
            customer_id = (info.data or {}).get("customer_id")
            if marker_v2:
                from app.core.config import settings
                marker_scope, key_hash, fingerprint, signature = marker_v2.groups()
                message = f"order-idempotency-v2:{marker_scope}:{key_hash}:{fingerprint}".encode()
                expected = hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()
                if hmac.compare_digest(signature, expected):
                    return v[:marker_v2.start()] or None
            elif marker_v1 and customer_id is not None:
                from app.core.config import settings
                key_hash, fingerprint, signature = marker_v1.groups()
                message = f"order-idempotency-v1:{customer_id}:{key_hash}:{fingerprint}".encode()
                expected = hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()
                if hmac.compare_digest(signature, expected):
                    return v[:marker_v1.start()] or None
        return v

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# Aliases for compatibility
OrderRead = OrderOut
OrderResponse = OrderOut


from app.models.order import OrderStatusEnum

class OrderStatusUpdate(BaseModel):
    status: OrderStatusEnum


class OrderDateUpdate(BaseModel):
    fulfillment_date: datetime

    @field_validator("fulfillment_date", mode="after")
    @classmethod
    def validate_fulfillment_date(cls, v):
        from app.utils.business_date import validate_fulfillment_date as _validate
        return _validate(v)


class OrderStatsOut(BaseModel):
    total_orders: int = 0
    pending: int = 0
    in_process: int = 0
    ready: int = 0
    delivered: int = 0
    picked_up: int = 0
    cancelled: int = 0
    refunded: int = 0
    active_orders: int = 0
    completed_orders: int = 0
    total_revenue: Decimal = Decimal("0.00")

    @field_validator("total_revenue", mode="before")
    @classmethod
    def round_revenue(cls, v):
        return _round2(v, default=Decimal("0.00"))

    model_config = ConfigDict(from_attributes=True)
