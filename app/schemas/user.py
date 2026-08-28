from pydantic import BaseModel, Field
from typing import Optional

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


class UserOut(BaseModel):
    id: int
    username: str
    role_id: int
    nomor_wa_admin: Optional[str] = None
    handles_takeover: bool
    is_active: bool

    class Config:
        from_attributes = True


class UserBootstrap(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    nomor_wa_admin: Optional[str] = None
