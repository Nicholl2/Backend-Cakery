from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class FaqCreate(BaseModel):
    """Schema for creating FAQ item"""
    pertanyaan: str = Field(..., min_length=5, max_length=500)
    jawaban: str = Field(..., min_length=10, max_length=2000)
    is_active: bool = True


class FaqUpdate(BaseModel):
    """Schema for updating FAQ item"""
    pertanyaan: Optional[str] = Field(None, min_length=5, max_length=500)
    jawaban: Optional[str] = Field(None, min_length=10, max_length=2000)
    is_active: Optional[bool] = None


class FaqResponse(BaseModel):
    """FAQ item response"""
    id: int
    pertanyaan: str
    jawaban: str
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class FaqDetailResponse(FaqResponse):
    """FAQ item with creator username"""
    created_by_username: str