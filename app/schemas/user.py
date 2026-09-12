from pydantic import BaseModel, ConfigDict, Field, field_validator, computed_field
from typing import Optional
from app.utils.phone import validate_phone_e164
from app.utils.sanitize import sanitize_text, USERNAME_PATTERN


class UserTakeoverUpdate(BaseModel):
    handles_takeover: bool = Field(..., description="Whether user handles takeover")


class UserTakeoverResponse(BaseModel):
    id: int
    username: str
    handles_takeover: bool

    model_config = ConfigDict(from_attributes=True)


from pydantic import AliasChoices

class UserCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Field ekstra untuk compatibility FE
    full_name: Optional[str] = Field(None, validation_alias=AliasChoices("full_name", "nama_lengkap"))
    role: Optional[str] = Field(None, description="Nama role (admin, staff, owner) jika tidak menggunakan role_id")
    
    username: str = Field(..., min_length=3, max_length=25, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=6)
    role_id: Optional[int] = Field(None, validation_alias=AliasChoices("role_id", "roleId"))
    
    nomor_wa_admin: Optional[str] = Field(None, validation_alias=AliasChoices("nomor_wa_admin", "nomor_wa"))
    handles_takeover: Optional[bool] = False
    is_active: Optional[bool] = True
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=16, validation_alias=AliasChoices("phone_number", "phone"))

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        return validate_phone_e164(v)

    @field_validator("username", mode="after")
    @classmethod
    def sanitize_username(cls, v):
        return sanitize_text(v)


class UserOut(BaseModel):
    id: int
    username: str
    role_id: int
    role_name: Optional[str] = None
    nomor_wa_admin: Optional[str] = None
    handles_takeover: bool
    is_active: bool
    email: Optional[str] = None
    phone_number: Optional[str] = None
    avatar_url: Optional[str] = None

    @computed_field
    @property
    def role(self) -> Optional[str]:
        return self.role_name

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)



class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: Optional[str] = Field(None, min_length=3, max_length=25, pattern=USERNAME_PATTERN)
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=16, validation_alias=AliasChoices("phone_number", "phone"))
    nomor_wa_admin: Optional[str] = Field(None, validation_alias=AliasChoices("nomor_wa_admin", "nomor_wa"))

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        if v is None:
            return v
        return validate_phone_e164(v)

    @field_validator("username", mode="after")
    @classmethod
    def sanitize_username(cls, v):
        if v is None:
            return v
        return sanitize_text(v)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, description="Password lama saat ini")
    new_password: str = Field(..., min_length=6, description="Password baru minimal 6 karakter")


class UserAdminUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: Optional[str] = Field(None, min_length=3, max_length=25, pattern=USERNAME_PATTERN)
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=16, validation_alias=AliasChoices("phone_number", "phone"))
    nomor_wa_admin: Optional[str] = Field(None, validation_alias=AliasChoices("nomor_wa_admin", "nomor_wa"))
    role_id: Optional[int] = Field(None, validation_alias=AliasChoices("role_id", "roleId"))
    role: Optional[str] = Field(None, description="Role name (owner, admin, staff)")
    handles_takeover: Optional[bool] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, description="Reset password baru oleh Owner")

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        if v is None:
            return v
        return validate_phone_e164(v)

    @field_validator("username", mode="after")
    @classmethod
    def sanitize_username(cls, v):
        if v is None:
            return v
        return sanitize_text(v)



class UserBootstrap(BaseModel):
    username: str = Field(..., min_length=3, max_length=25, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=6)
    nomor_wa_admin: Optional[str] = None
    email: Optional[str] = Field(None, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=16)

    @field_validator("nomor_wa_admin", mode="before")
    @classmethod
    def validate_nomor_wa(cls, v):
        return validate_phone_e164(v)

    @field_validator("username", mode="after")
    @classmethod
    def sanitize_username(cls, v):
        return sanitize_text(v)
