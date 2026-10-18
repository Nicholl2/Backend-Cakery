from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PaymentMethod = Literal["cash", "bank_transfer", "e_wallet", "card", "other"]


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class ExpenseCreate(BaseModel):
    kategori: str = Field(..., min_length=3, max_length=50)
    jumlah: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    tanggal: Optional[datetime] = None
    deskripsi: Optional[str] = Field(None, max_length=500)
    penerima: Optional[str] = Field(None, max_length=120)
    metode_pembayaran: Optional[PaymentMethod] = None
    bukti_url: Optional[str] = Field(None, max_length=2048)

    model_config = ConfigDict(extra="forbid")

    @field_validator("kategori")
    @classmethod
    def validate_category(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("Kategori minimal 3 karakter")
        return cleaned

    @field_validator("deskripsi", "penerima", mode="before")
    @classmethod
    def clean_text(cls, value):
        return _clean_optional_text(value)

    @field_validator("bukti_url")
    @classmethod
    def validate_receipt_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        parsed = urlsplit(value.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("bukti_url harus berupa URL HTTPS yang valid")
        return value.strip()


class ExpenseUpdate(BaseModel):
    kategori: Optional[str] = Field(None, min_length=3, max_length=50)
    jumlah: Optional[Decimal] = Field(None, gt=0, max_digits=15, decimal_places=2)
    tanggal: Optional[datetime] = None
    deskripsi: Optional[str] = Field(None, max_length=500)
    penerima: Optional[str] = Field(None, max_length=120)
    metode_pembayaran: Optional[PaymentMethod] = None
    bukti_url: Optional[str] = Field(None, max_length=2048)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("Minimal satu field expense harus diubah")
        if "kategori" in self.model_fields_set:
            if self.kategori is None or len(self.kategori.strip()) < 3:
                raise ValueError("Kategori minimal 3 karakter")
            self.kategori = self.kategori.strip()
        if "jumlah" in self.model_fields_set and self.jumlah is None:
            raise ValueError("Jumlah tidak boleh kosong")
        if "tanggal" in self.model_fields_set and self.tanggal is None:
            raise ValueError("Tanggal tidak boleh kosong")
        for field_name in ("deskripsi", "penerima"):
            if field_name in self.model_fields_set:
                setattr(self, field_name, _clean_optional_text(getattr(self, field_name)))
        if "bukti_url" in self.model_fields_set and self.bukti_url is not None:
            parsed = urlsplit(self.bukti_url.strip())
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("bukti_url harus berupa URL HTTPS yang valid")
            self.bukti_url = self.bukti_url.strip()
        return self


class ExpenseCancel(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)

    model_config = ConfigDict(extra="forbid")

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("Alasan pembatalan minimal 3 karakter")
        return cleaned


class ExpenseResponse(BaseModel):
    id: int
    kategori: str
    jumlah: Decimal
    recorded_by: int
    tanggal: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None
    deskripsi: Optional[str] = None
    penerima: Optional[str] = None
    metode_pembayaran: Optional[PaymentMethod] = None
    bukti_url: Optional[str] = None
    is_voided: bool = False
    void_reason: Optional[str] = None
    voided_by: Optional[int] = None
    voided_at: Optional[datetime] = None
    updated_by: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ExpenseDetailResponse(ExpenseResponse):
    recorded_by_username: str


class ExpenseAuditResponse(BaseModel):
    id: int
    expense_id: int
    action: Literal["created", "updated", "voided"]
    actor_id: Optional[int] = None
    changes: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
