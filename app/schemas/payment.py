from pydantic import BaseModel, Field, field_validator
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from datetime import datetime


def _round2(v) -> Optional[Decimal]:
    if v is None:
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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

    class Config:
        from_attributes = True