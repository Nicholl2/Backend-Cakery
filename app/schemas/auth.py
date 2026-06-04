from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional


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