from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from typing import Optional
from datetime import datetime
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


class ReviewProductOut(BaseModel):
    """Product fields safe to expose alongside a public review."""
    nama_produk: str
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


ReviewPublicProductOut = ReviewProductOut


class ReviewCustomerOut(BaseModel):
    """A public display name only; customer identifiers and contact data stay private."""
    nama: str = "Customer"

    model_config = ConfigDict(from_attributes=True)


ReviewPublicCustomerOut = ReviewCustomerOut


class ReviewPublicOut(BaseModel):
    """Public review contract with no order, buyer, or internal product data."""
    id: int
    product_id: int
    rating: int
    komentar: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime
    images: list[ReviewImageOut] = Field(default_factory=list)
    product_name: Optional[str] = None
    customer_name: Optional[str] = None
    product: Optional[ReviewProductOut] = None
    customer: Optional[ReviewCustomerOut] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def populate_public_names(self):
        if self.comment is None and self.komentar is not None:
            self.comment = self.komentar
        elif self.komentar is None and self.comment is not None:
            self.komentar = self.comment
        if self.product_name is None and self.product is not None:
            self.product_name = self.product.nama_produk
        if self.customer_name is None and self.customer is not None:
            self.customer_name = self.customer.nama
        if not self.customer_name:
            self.customer_name = "Pelanggan"
        if not self.product_name:
            self.product_name = "Produk Toti"
        return self


# Backwards-compatible schema name. Keep it safe so new routes cannot
# accidentally reintroduce customer contact or internal product fields.
ReviewOut = ReviewPublicOut
