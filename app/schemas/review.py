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


class ReviewImageOut(BaseModel):
    id: int
    review_id: Optional[int] = None
    image_url: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReviewOut(BaseModel):
    id: int
    order_id: int
    product_id: int
    customer_id: int
    rating: int
    komentar: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime
    images: list[ReviewImageOut] = Field(default_factory=list)

    # Direct name fields for frontend convenience
    product_name: Optional[str] = None
    customer_name: Optional[str] = None

    # Nested response objects
    product: Optional[ProductOut] = None
    customer: Optional[CustomerOut] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def populate_extra_fields(self):
        if self.comment is None and self.komentar is not None:
            self.comment = self.komentar
        elif self.komentar is None and self.comment is not None:
            self.komentar = self.comment
        if self.product_name is None and self.product is not None:
            self.product_name = getattr(self.product, "nama_produk", None) or getattr(self.product, "name", None)
        if self.customer_name is None and self.customer is not None:
            self.customer_name = getattr(self.customer, "nama", None)
        return self


