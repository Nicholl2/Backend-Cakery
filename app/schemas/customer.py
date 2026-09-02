from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional
from app.utils.phone import validate_phone_e164


class CustomerUpsert(BaseModel):
    nama: str = Field(..., min_length=1, max_length=100)
    nomor_wa: str = Field(..., description="Nomor WhatsApp customer E.164 (7-15 digit)")
    alamat: Optional[str] = None

    @field_validator("nomor_wa", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        return validate_phone_e164(v)


class CustomerOut(BaseModel):
    id: int
    nama: str
    nomor_wa: str
    alamat: Optional[str] = None
    is_verified: bool
    human_takeover_active: bool
    takeover_expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TakeoverSet(BaseModel):
    active: bool
    expires_at: Optional[datetime] = Field(
        None,
        description="Wajib diisi jika active=True. Diabaikan jika active=False."
    )


class TakeoverStatus(BaseModel):
    nomor_wa: str
    human_takeover_active: bool
    takeover_expires_at: Optional[datetime] = None
    is_expired: bool = Field(
        description="True jika takeover aktif tapi expires_at sudah lewat"
    )