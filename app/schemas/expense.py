from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime
from typing import Optional


class ExpenseCreate(BaseModel):
    """Schema for creating expense"""
    kategori: str = Field(..., min_length=3, max_length=50)
    jumlah: Decimal = Field(..., gt=0, decimal_places=2)
    tanggal: Optional[datetime] = None


class ExpenseUpdate(BaseModel):
    """Schema for updating expense"""
    kategori: Optional[str] = Field(None, min_length=3, max_length=50)
    jumlah: Optional[Decimal] = Field(None, gt=0, decimal_places=2)


class ExpenseResponse(BaseModel):
    """Expense response"""
    id: int
    kategori: str
    jumlah: Decimal
    recorded_by: int
    tanggal: datetime
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ExpenseDetailResponse(ExpenseResponse):
    """Expense with recorder username"""
    recorded_by_username: str