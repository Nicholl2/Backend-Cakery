from pydantic import BaseModel, Field, field_validator

from app.utils.email_identity import normalize_email_address, normalize_sender_name


class EmailSenderSettingsUpdate(BaseModel):
    from_email: str = Field(..., min_length=6, max_length=255)
    from_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("from_email", mode="before")
    @classmethod
    def validate_email(cls, value):
        return normalize_email_address(value)

    @field_validator("from_name", mode="before")
    @classmethod
    def validate_name(cls, value):
        return normalize_sender_name(value)


class EmailSenderSettingsOut(BaseModel):
    from_email: str
    from_name: str
    updated_at: str | None = None
