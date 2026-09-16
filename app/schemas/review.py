from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from typing import Optional
from datetime import datetime
from app.schemas.product import ProductOut
from app.schemas.customer import CustomerOut
from app.utils.sanitize import sanitize_text


class ReviewCreate(BaseModel):
    order_id: int = Field(..., description="ID Pesanan yang telah selesai")
    product_id: int = Field(..., ge=1, description="ID Produk yang diulas")
    rating: int = Field(..., ge=1, le=5, description="Rating dari 1 s/d 5")
    comment: Optional[str] = Field(None, max_length=1000, description="Ulasan produk")
    komentar: Optional[str] = Field(None, max_length=1000, description="Alias komentar ulasan")

    @field_validator("comment", "komentar", mode="before")
    @classmethod
    def sanitize_comment_input(cls, v):
        if v is None:
            return v
        return sanitize_text(v)

    @model_validator(mode="after")
    def validate_and_sync_comment(self):
        if not self.comment and not self.komentar:
            raise ValueError("Ulasan / comment wajib diisi.")
        if self.comment is None and self.komentar is not None:
            self.comment = self.komentar
        elif self.komentar is None and self.comment is not None:
            self.komentar = self.comment
        return self


class ReviewUpdate(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)
    komentar: Optional[str] = Field(None, max_length=1000)

    @field_validator("comment", "komentar", mode="before")
    @classmethod
    def sanitize_comment_input(cls, v):
        if v is None:
            return v
        return sanitize_text(v)

    @model_validator(mode="after")
    def sync_comment(self):
        if self.comment is not None and self.komentar is None:
            self.komentar = self.comment
        elif self.komentar is not None and self.comment is None:
            self.comment = self.komentar
        return self


class ReviewOut(BaseModel):
    id: int
    order_id: int
    product_id: int
    customer_id: int
    rating: int
    komentar: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime

    # Nested response objects
    product: Optional[ProductOut] = None
    customer: Optional[CustomerOut] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def populate_comment(self):
        if self.comment is None and self.komentar is not None:
            self.comment = self.komentar
        elif self.komentar is None and self.comment is not None:
            self.komentar = self.comment
        return self

