import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import NO_VALUE

from app.core.config import settings
from app.models.email_outbox import EmailOutbox
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.models.order import Order


def _json_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


async def enqueue_email(db: AsyncSession, *, event_key: str, recipient: str,
                        event_type: str, payload: dict) -> None:
    """Add an idempotent email event to the caller's current transaction."""
    if not recipient:
        return
    values = dict(event_key=event_key, recipient=recipient, event_type=event_type,
                  payload_json=_json_payload(payload))
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = pg_insert(EmailOutbox).values(**values).on_conflict_do_nothing(
            index_elements=[EmailOutbox.event_key]
        )
        await db.execute(stmt)
    else:
        existing = await db.execute(select(EmailOutbox.id).where(EmailOutbox.event_key == event_key))
        if existing.scalar_one_or_none() is None:
            db.add(EmailOutbox(**values))
            await db.flush()


def order_email_payload(order: Order, **extra) -> dict:
    customer = inspect(order).attrs.customer.loaded_value
    return {
        "order_id": order.id,
        "order_number": f"ORDER-{order.id}",
        "total": str(Decimal(str(order.total_harga_pesanan or 0))),
        "customer_name": customer.nama if customer is not NO_VALUE and customer else "Pelanggan",
        **extra,
    }


async def enqueue_new_order_for_sellers(db: AsyncSession, order: Order) -> None:
    result = await db.execute(
        select(User.email).join(Role, User.role_id == Role.id)
        .where(User.is_active.is_(True), Role.level.in_([1, 2, 3]), User.email.is_not(None))
    )
    recipients = sorted({email.strip() for (email,) in result.all() if email and email.strip()})
    if not recipients and settings.smtp_operational_inbox.strip():
        recipients = [settings.smtp_operational_inbox.strip()]
    for recipient in recipients:
        await enqueue_email(
            db, event_key=f"order-created:{order.id}:{recipient.lower()}", recipient=recipient,
            event_type="order_created", payload=order_email_payload(order),
        )


async def buyer_email_for_order(db: AsyncSession, order: Order) -> str | None:
    customer = inspect(order).attrs.customer.loaded_value
    if customer is NO_VALUE and order.customer_id:
        customer = await db.get(Customer, order.customer_id)
    if not customer or not customer.nomor_wa:
        return None
    result = await db.execute(select(Buyer).where(Buyer.phone == customer.nomor_wa, Buyer.is_active.is_(True)))
    buyer = result.scalars().first()
    if buyer:
        return buyer.email

    # Legacy Customer rows may contain a different phone formatting variant.
    from app.utils.phone import normalize_phone
    customer_phone = normalize_phone(customer.nomor_wa, as_http_exception=False)
    buyers = (await db.execute(select(Buyer).where(Buyer.is_active.is_(True)))).scalars().all()
    return next((buyer.email for buyer in buyers
                 if normalize_phone(buyer.phone, as_http_exception=False) == customer_phone), None)


async def enqueue_order_status_for_buyer(db: AsyncSession, order: Order, new_status: str) -> None:
    recipient = await buyer_email_for_order(db, order)
    if recipient:
        await enqueue_email(
            db, event_key=f"order-status:{order.id}:{new_status}", recipient=recipient,
            event_type="order_status_updated",
            payload=order_email_payload(order, status=new_status),
        )


async def enqueue_order_overdue_for_buyer(db: AsyncSession, order: Order) -> None:
    recipient = await buyer_email_for_order(db, order)
    if recipient:
        await enqueue_email(
            db, event_key=f"order-overdue:buyer:{order.id}", recipient=recipient,
            event_type="order_overdue", payload=order_email_payload(order),
        )


async def enqueue_order_overdue_for_sellers(db: AsyncSession, order: Order) -> None:
    result = await db.execute(
        select(User.email).join(Role, User.role_id == Role.id)
        .where(User.is_active.is_(True), Role.level.in_([1, 2, 3]), User.email.is_not(None))
    )
    recipients = sorted({email.strip() for (email,) in result.all() if email and email.strip()})
    if not recipients and settings.smtp_operational_inbox.strip():
        recipients = [settings.smtp_operational_inbox.strip()]
    for recipient in recipients:
        await enqueue_email(
            db, event_key=f"order-overdue:seller:{order.id}:{recipient.lower()}", recipient=recipient,
            event_type="order_overdue", payload=order_email_payload(order),
        )


async def enqueue_payment_event(db: AsyncSession, order: Order, event_type: str,
                                event_reference: str, **extra) -> None:
    recipient = await buyer_email_for_order(db, order)
    if recipient:
        await enqueue_email(
            db, event_key=f"{event_type}:{event_reference}:{recipient.lower()}",
            recipient=recipient, event_type=event_type,
            payload=order_email_payload(order, **extra),
        )


async def enqueue_order_payment_event(db: AsyncSession, order_id: int, event_type: str,
                                      event_reference: str, **extra) -> None:
    result = await db.execute(select(Order).where(Order.id == order_id).options(selectinload(Order.customer)))
    order = result.scalars().first()
    if order:
        await enqueue_payment_event(db, order, event_type, event_reference, **extra)
