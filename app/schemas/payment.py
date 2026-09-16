from pydantic import BaseModel, Field, field_validator, ConfigDict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union
from datetime import datetime


def _round2(v) -> Optional[Decimal]:
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ManualPaymentRequest(BaseModel):
    order_id: str = Field(..., description="ID pesanan yang dibayar")
    amount: float = Field(..., gt=0, description="Nominal pembayaran")
    payment_method: str = Field(..., description="Metode pembayaran manual, e.g., CASH, TRANSFER")
    notes: Optional[str] = Field(None, description="Catatan pembayaran tambahan")

    @field_validator("order_id", mode="before")
    @classmethod
    def stringify_order_id(cls, v):
        return str(v)


class ManualPaymentResponse(BaseModel):
    success: bool = True
    message: str = "Pembayaran manual berhasil dicatat"
    payment_id: int
    order_id: int
    amount: Decimal
    payment_method: str
    payment_status: str = "PAID"
    order_status: str

    @field_validator("amount", mode="before")
    @classmethod
    def round_amount(cls, v):
        return _round2(v)

    model_config = ConfigDict(from_attributes=True)


class PaymentCreate(BaseModel):
    invoice_id: int
    jumlah_bayar: Decimal = Field(..., gt=0, decimal_places=2)
    payment_method: str = Field(..., max_length=50)
    payment_type: str = Field(default="Final", pattern="^(DP|Final)$")
    pg_transaction_id: Optional[str] = None


class PaymentOut(BaseModel):
    id: int
    invoice_id: int
    pg_transaction_id: Optional[str] = None
    jumlah_bayar: Decimal
    payment_method: str
    payment_status: str
    payment_type: str
    verified_by: Optional[int] = None
    created_at: Optional[datetime] = None

    @field_validator("jumlah_bayar", mode="before")
    @classmethod
    def round_money(cls, v):
        return _round2(v)

    model_config = ConfigDict(from_attributes=True)