from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional
from datetime import datetime
from app.schemas.product import ProductOut
from app.schemas.customer import CustomerOut
from app.utils.sanitize import sanitize_text


class ReviewCreate(BaseModel):
    product_id: int = Field(..., ge=1)
    rating: int = Field(..., ge=1, le=5, description="Rating dari 1 s/d 5")
    komentar: Optional[str] = Field(None, max_length=1000)

    @field_validator("komentar", mode="before")
    @classmethod
    def sanitize_komentar(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    komentar: Optional[str] = Field(None, max_length=1000)

    @field_validator("komentar", mode="before")
    @classmethod
    def sanitize_komentar(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class ReviewOut(BaseModel):
    id: int
    product_id: int
    customer_id: int
    rating: int
    komentar: Optional[str] = None
    created_at: datetime

    # Nested response objects
    product: Optional[ProductOut] = None
    customer: Optional[CustomerOut] = None

    model_config = ConfigDict(from_attributes=True)
