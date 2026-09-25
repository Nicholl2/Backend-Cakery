from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_db
from app.api.dependencies import (
    get_auth_identity_optional_service_or_jwt,
    AuthIdentity,
)
from app.schemas.notification import NotificationOut, UnreadCountOut
from app.repositories import notification_repo

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden"},
    },
)


@router.get("", response_model=List[NotificationOut], summary="Get notifications for authenticated user/buyer")
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
) -> List[NotificationOut]:
    if auth.is_buyer:
        notifications = await notification_repo.get_notifications_for_buyer(
            db, buyer_id=auth.user_id, limit=limit, offset=offset
        )
    elif auth.auth_type == "user":
        notifications = await notification_repo.get_notifications_for_user(
            db, user_id=auth.user_id, limit=limit, offset=offset
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return [NotificationOut.model_validate(n) for n in notifications]


@router.get("/unread-count", response_model=UnreadCountOut, summary="Get unread notification count")
async def get_unread_count(
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
) -> UnreadCountOut:
    if auth.is_buyer:
        count = await notification_repo.get_unread_count_for_buyer(db, buyer_id=auth.user_id)
    elif auth.auth_type == "user":
        count = await notification_repo.get_unread_count_for_user(db, user_id=auth.user_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return UnreadCountOut(unread_count=count)


@router.patch("/{notification_id}/read", response_model=NotificationOut, summary="Mark single notification as read")
async def mark_notification_read(
    notification_id: int,
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
) -> NotificationOut:
    if auth.is_buyer:
        notification = await notification_repo.mark_as_read(
            db, notification_id=notification_id, recipient_buyer_id=auth.user_id
        )
    elif auth.auth_type == "user":
        notification = await notification_repo.mark_as_read(
            db, notification_id=notification_id, recipient_user_id=auth.user_id
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    return NotificationOut.model_validate(notification)


@router.post("/read-all", summary="Mark all notifications as read for current user")
async def mark_all_notifications_read(
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
):
    if auth.is_buyer:
        count = await notification_repo.mark_all_as_read(db, recipient_buyer_id=auth.user_id)
    elif auth.auth_type == "user":
        count = await notification_repo.mark_all_as_read(db, recipient_user_id=auth.user_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return {"status": "success", "marked_read_count": count}
