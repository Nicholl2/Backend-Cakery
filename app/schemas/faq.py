from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime
from typing import Optional


def _validate_faq_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("FAQ text must be a string")
    value = value.strip()
    if not value:
        raise ValueError("FAQ text must not be blank")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("FAQ text contains unsupported control characters")
    return value


class FaqCreate(BaseModel):
    """Schema for creating FAQ item"""
    pertanyaan: str = Field(..., min_length=5, max_length=500)
    jawaban: str = Field(..., min_length=10, max_length=2000)
    pertanyaan_en: Optional[str] = Field(None, min_length=5, max_length=500)
    jawaban_en: Optional[str] = Field(None, min_length=10, max_length=2000)
    is_active: bool = True

    @field_validator("pertanyaan", "jawaban", "pertanyaan_en", "jawaban_en", mode="before")
    @classmethod
    def validate_text(cls, value: Optional[str]) -> Optional[str]:
        return _validate_faq_text(value)


class FaqUpdate(BaseModel):
    """Schema for updating FAQ item"""
    pertanyaan: Optional[str] = Field(None, min_length=5, max_length=500)
    jawaban: Optional[str] = Field(None, min_length=10, max_length=2000)
    pertanyaan_en: Optional[str] = Field(None, min_length=5, max_length=500)
    jawaban_en: Optional[str] = Field(None, min_length=10, max_length=2000)
    is_active: Optional[bool] = None

    @field_validator("pertanyaan", "jawaban", "pertanyaan_en", "jawaban_en", mode="before")
    @classmethod
    def validate_text(cls, value: Optional[str]) -> Optional[str]:
        return _validate_faq_text(value)


class FaqResponse(BaseModel):
    """FAQ item response"""
    id: int
    pertanyaan: str
    jawaban: str
    pertanyaan_en: Optional[str] = None
    jawaban_en: Optional[str] = None
    created_by: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FaqDetailResponse(FaqResponse):
    """FAQ item with creator username"""
    created_by_username: str
