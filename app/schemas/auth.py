from pydantic import BaseModel, Field, field_validator, model_validator
from decimal import Decimal
from typing import Optional
from enum import Enum
from app.utils.phone import validate_phone_e164


class UserLogin(BaseModel):
    """Schema for user login request (accepts username, email, phone number, or identifier)"""
    identifier: Optional[str] = Field(None, min_length=3, max_length=100, description="Username, email, atau nomor HP")
    username: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=25)
    phone_number: Optional[str] = Field(None, max_length=25)
    password: str = Field(..., min_length=6)

    @model_validator(mode="before")
    @classmethod
    def resolve_identifier(cls, values):
        if isinstance(values, dict):
            id_val = values.get("identifier") or values.get("username") or values.get("email") or values.get("phone") or values.get("phone_number")
            if id_val:
                values["identifier"] = id_val
            else:
                raise ValueError("Identifier (username/email/phone) harus diisi")
        return values


class Token(BaseModel):
    """JWT Token response"""
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role_level: int
    username: str


class UserResponse(BaseModel):
    """User data response"""
    id: int
    username: str
    is_active: bool
    role_id: int
    nomor_wa_admin: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None

    class Config:
        from_attributes = True


# ── BUYER / SELLER AUTH SCHEMAS ─────────────────────────────────────────────

class OTPChannel(str, Enum):
    whatsapp = "whatsapp"
    email = "email"


class OTPPurpose(str, Enum):
    register = "register"
    login = "login"
    reset_password = "reset_password"


class OTPSendRequest(BaseModel):
    target: str = Field(..., description="Email address or WhatsApp phone number")
    channel: OTPChannel
    purpose: OTPPurpose


class OTPSendResponse(BaseModel):
    otp_id: str
    expires_in: int = 300  # seconds


class OTPVerifyRequest(BaseModel):
    otp_id: str
    code: str


class OTPVerifyResponse(BaseModel):
    verify_token: str
    target: str


class BuyerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=100)
    phone: Optional[str] = Field(None, max_length=25)
    phone_number: Optional[str] = Field(None, max_length=25)
    password: str = Field(..., min_length=6)
    verify_token: str

    @field_validator("phone", "phone_number", mode="before")
    @classmethod
    def validate_phones(cls, v):
        return validate_phone_e164(v)


class BuyerAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str = "buyer"
    name: str
    email: str
    phone: str


class BuyerLoginRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None
    phone_number: Optional[str] = None
    verify_token: Optional[str] = None

    @field_validator("phone", "phone_number", mode="before")
    @classmethod
    def validate_phones(cls, v):
        return validate_phone_e164(v)


class BuyerLoginPhoneRequest(BaseModel):
    phone_number: str = Field(..., description="Nomor telepon E.164 (7-15 digit)")
    password: str

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return validate_phone_e164(v)


class BuyerLoginOTPRequest(BaseModel):
    phone: Optional[str] = None
    phone_number: Optional[str] = None
    verify_token: str

    @field_validator("phone", "phone_number", mode="before")
    @classmethod
    def validate_phones(cls, v):
        return validate_phone_e164(v)


class BuyerResetPasswordRequest(BaseModel):
    verify_token: str
    new_password: str = Field(..., min_length=6)


class SellerForgotPasswordRequest(BaseModel):
    email: str


class SellerForgotPasswordVerifyRequest(BaseModel):
    otp_id: str
    code: str


class SellerResetPasswordRequest(BaseModel):
    verify_token: str
    new_password: str = Field(..., min_length=6)


# ── WA DEEP LINK OTP SCHEMAS ────────────────────────────────────────────────

class WAVerifyStartRequest(BaseModel):
    phone_number: str = Field(..., description="Nomor telepon E.164 (7-15 digit)")

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v):
        return validate_phone_e164(v)


class WAVerifyStartResponse(BaseModel):
    nonce: str
    deeplink: str = ""
    expires_in: int
    verify_token: Optional[str] = None
    mock_mode: bool = False


class WAVerifyConfirmRequest(BaseModel):
    nonce: str
    sender_phone: str = Field(..., description="Nomor telepon pengirim pesan WA")

    @field_validator("sender_phone", mode="before")
    @classmethod
    def validate_sender(cls, v):
        return validate_phone_e164(v)


class WAVerifyStatusResponse(BaseModel):
    status: str
    verify_token: Optional[str] = None