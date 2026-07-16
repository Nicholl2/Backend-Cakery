from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from enum import Enum


class UserLogin(BaseModel):
    """Schema for user login request"""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


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
    phone: str = Field(..., max_length=20)
    password: str = Field(..., min_length=6)
    verify_token: str


class BuyerAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str = "buyer"
    name: str
    email: str
    phone: str


class BuyerLoginRequest(BaseModel):
    email: str
    password: str


class BuyerLoginOTPRequest(BaseModel):
    phone: str
    verify_token: str


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