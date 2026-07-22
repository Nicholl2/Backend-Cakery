from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.schemas.product import ProductOut
from app.schemas.customer import CustomerOut


class ReviewCreate(BaseModel):
    product_id: int = Field(..., ge=1)
    rating: int = Field(..., ge=1, le=5, description="Rating dari 1 s/d 5")
    komentar: Optional[str] = Field(None, max_length=1000)


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    komentar: Optional[str] = Field(None, max_length=1000)


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

    class Config:
        from_attributes = True
