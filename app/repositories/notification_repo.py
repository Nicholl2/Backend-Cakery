from datetime import datetime, timezone
from typing import Optional, Sequence
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, and_
from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    type: str,
    recipient_buyer_id: Optional[int] = None,
    recipient_user_id: Optional[int] = None,
    order_id: Optional[int] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[int] = None,
    metadata_json: Optional[str] = None,
) -> Notification:
    notification = Notification(
        recipient_buyer_id=recipient_buyer_id,
        recipient_user_id=recipient_user_id,
        type=type,
        order_id=order_id,
        actor_type=actor_type,
        actor_id=actor_id,
        metadata_json=metadata_json,
    )
    db.add(notification)
    await db.flush()
    return notification


async def get_notifications_for_buyer(
    db: AsyncSession,
    buyer_id: int,
    limit: int = 20,
    offset: int = 0,
) -> Sequence[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.recipient_buyer_id == buyer_id)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    res = await db.execute(stmt)
    return res.scalars().all()


async def get_notifications_for_user(
    db: AsyncSession,
    user_id: int,
    limit: int = 20,
    offset: int = 0,
) -> Sequence[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.recipient_user_id == user_id)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    res = await db.execute(stmt)
    return res.scalars().all()


async def get_unread_count_for_buyer(db: AsyncSession, buyer_id: int) -> int:
    stmt = (
        select(func.count(Notification.id))
        .where(
            Notification.recipient_buyer_id == buyer_id,
            Notification.read_at.is_(None),
        )
    )
    res = await db.execute(stmt)
    return res.scalar() or 0


async def get_unread_count_for_user(db: AsyncSession, user_id: int) -> int:
    stmt = (
        select(func.count(Notification.id))
        .where(
            Notification.recipient_user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    res = await db.execute(stmt)
    return res.scalar() or 0


async def mark_as_read(
    db: AsyncSession,
    notification_id: int,
    recipient_buyer_id: Optional[int] = None,
    recipient_user_id: Optional[int] = None,
) -> Optional[Notification]:
    conditions = [Notification.id == notification_id]
    if recipient_buyer_id is not None:
        conditions.append(Notification.recipient_buyer_id == recipient_buyer_id)
    if recipient_user_id is not None:
        conditions.append(Notification.recipient_user_id == recipient_user_id)

    stmt = select(Notification).where(and_(*conditions))
    res = await db.execute(stmt)
    notification = res.scalars().first()

    if notification and notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(notification)

    return notification


async def mark_all_as_read(
    db: AsyncSession,
    recipient_buyer_id: Optional[int] = None,
    recipient_user_id: Optional[int] = None,
) -> int:
    now = datetime.now(timezone.utc)
    if recipient_buyer_id is not None:
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_buyer_id == recipient_buyer_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
        )
    elif recipient_user_id is not None:
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_user_id == recipient_user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
        )
    else:
        return 0

    res = await db.execute(stmt)
    await db.commit()
    return res.rowcount
