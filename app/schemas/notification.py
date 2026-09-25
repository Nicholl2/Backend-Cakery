from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, Any


class NotificationBase(BaseModel):
    type: str
    order_id: Optional[int] = None
    actor_type: Optional[str] = None
    actor_id: Optional[int] = None
    metadata_json: Optional[str] = None
    read_at: Optional[datetime] = None


class NotificationOut(BaseModel):
    id: int
    recipient_buyer_id: Optional[int] = None
    recipient_user_id: Optional[int] = None
    type: str
    order_id: Optional[int] = None
    actor_type: Optional[str] = None
    actor_id: Optional[int] = None
    metadata_json: Optional[str] = None
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnreadCountOut(BaseModel):
    unread_count: int
