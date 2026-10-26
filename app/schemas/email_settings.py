import re
from pydantic import BaseModel, Field, field_validator


class EmailSenderSettingsUpdate(BaseModel):
    from_email: str = Field(..., min_length=6, max_length=255)
    from_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("from_email", mode="before")
    @classmethod
    def validate_email(cls, value):
        if not isinstance(value, str):
            raise ValueError("Alamat email tidak valid")
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Alamat email tidak valid")
        return normalized

    @field_validator("from_name", mode="before")
    @classmethod
    def validate_name(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Nama pengirim wajib diisi")
        normalized = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise ValueError("Nama pengirim mengandung karakter kontrol")
        return normalized


class EmailSenderSettingsOut(BaseModel):
    from_email: str
    from_name: str
    updated_at: str | None = None

