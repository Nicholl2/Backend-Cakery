from pydantic import BaseModel, Field, field_validator
from typing import Optional
from app.utils.phone import validate_phone_e164

class UserTakeoverUpdate(BaseModel):
    handles_takeover: bool = Field(..., description="Whether user handles takeover")

class UserTakeoverResponse(BaseModel):
    id: int
    username: str
    handles_takeover: bool

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    role_id: int
    nomor_wa_admin: Optional[str] = None
    handles_takeover: Optional[bool] = False
    is_active: Optional[bool] = True
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=20)

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        return validate_phone_e164(v)


class UserOut(BaseModel):
    id: int
    username: str
    role_id: int
    nomor_wa_admin: Optional[str] = None
    handles_takeover: bool
    is_active: bool
    email: Optional[str] = None
    phone_number: Optional[str] = None

    class Config:
        from_attributes = True


class UserBootstrap(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    nomor_wa_admin: Optional[str] = None
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=20)

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        return validate_phone_e164(v)
