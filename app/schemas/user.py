from pydantic import BaseModel, Field

class UserTakeoverUpdate(BaseModel):
    handles_takeover: bool = Field(..., description="Whether user handles takeover")

class UserTakeoverResponse(BaseModel):
    id: int
    username: str
    handles_takeover: bool

    class Config:
        from_attributes = True
