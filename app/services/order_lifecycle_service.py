"""Durable, idempotent automation for unpaid and late orders."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.order import Invoice, InvoiceStatusEnum, Order, OrderStatusEnum, OrderItem
from app.models.payment import Payment, PaymentStatusEnum
from app.models.product import Product
from app.models.recipe import Recipe

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _expire_unpaid_candidates(db: AsyncSession, session_factory) -> int:
    now = datetime.now(timezone.utc)
    timeout = timedelta(minutes=settings.order_unpaid_timeout_minutes)
    result = await db.execute(
        select(Order.id)
        .join(Invoice, Invoice.order_id == Order.id)
        .where(
            Order.status == OrderStatusEnum.pending,
            Order.created_via != "seller",
            Invoice.status == InvoiceStatusEnum.unpaid,
            ((Order.payment_expires_at.is_not(None)) & (Order.payment_expires_at <= now))
            | ((Order.payment_expires_at.is_(None)) & (Order.created_at <= now - timeout)),
        )
        .order_by(Order.id)
    )
    order_ids = result.scalars().all()
    await db.rollback()
    cancelled = 0

    for order_id in order_ids:
        # Reconcile every live gateway transaction before releasing stock. An
        # unreachable or still-pending gateway is deliberately not treated as expired.
        async with session_factory() as payment_db:
            from app.services.payment_service import refresh_if_pending
            pending = (await payment_db.execute(
                select(Payment).join(Invoice, Invoice.id == Payment.invoice_id)
                .where(Invoice.order_id == order_id,
                       Payment.payment_status == PaymentStatusEnum.pending,
                       Payment.pg_order_id.is_not(None))
            )).scalars().all()
            for payment in pending:
                await refresh_if_pending(payment_db, payment)

        did_cancel = False
        async with session_factory() as order_db:
            async with order_db.begin():
                result = await order_db.execute(
                    select(Order).where(Order.id == order_id)
                    .options(
                        selectinload(Order.invoice),
                        selectinload(Order.order_items).selectinload(OrderItem.product)
                        .selectinload(Product.recipes).selectinload(Recipe.stock_item),
                    )
                    .with_for_update().execution_options(populate_existing=True)
                )
                order = result.scalars().first()
                if not order or not order.invoice or order.status != OrderStatusEnum.pending:
                    continue
                if order.created_via == "seller" or order.invoice.status != InvoiceStatusEnum.unpaid:
                    continue
                expires_at = order.payment_expires_at or (order.created_at + timeout if order.created_at else None)
                if expires_at:
                    expires_at = _as_utc(expires_at)
                if not expires_at or expires_at > now:
                    continue
                active_payment = await order_db.scalar(
                    select(Payment.id).where(
                        Payment.invoice_id == order.invoice.id,
                        Payment.payment_status == PaymentStatusEnum.pending,
                        Payment.pg_order_id.is_not(None),
                    ).limit(1)
                )
                successful_payment = await order_db.scalar(
                    select(Payment.id).where(
                        Payment.invoice_id == order.invoice.id,
                        Payment.payment_status == PaymentStatusEnum.success,
                    ).limit(1)
                )
                if active_payment or successful_payment:
                    continue

                from app.services.order_service import _rollback_order_stock
                from app.services.email_outbox_service import enqueue_order_status_for_buyer
                from app.services.notification_service import notify_order_event
                await _rollback_order_stock(order_db, order)
                order.status = OrderStatusEnum.cancelled
                await enqueue_order_status_for_buyer(order_db, order, OrderStatusEnum.cancelled.value)
                await notify_order_event(order_db, order, "order_cancelled_unpaid")
                did_cancel = True
        if did_cancel:
            cancelled += 1
            from app.services.chatbot_notify import notify_chatbot_order_event
            await notify_chatbot_order_event(order_id, "cancelled")

    return cancelled


async def _notify_overdue_candidates(db: AsyncSession, session_factory) -> int:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Order.id).join(Invoice, Invoice.order_id == Order.id).where(
            Order.fulfillment_date.is_not(None), Order.fulfillment_date <= now,
            Order.fulfillment_overdue_notified_at.is_(None),
            Order.status.in_([OrderStatusEnum.pending, OrderStatusEnum.in_process, OrderStatusEnum.ready]),
            Invoice.status.in_([InvoiceStatusEnum.partial, InvoiceStatusEnum.paid]),
        ).order_by(Order.id)
    )
    order_ids = result.scalars().all()
    await db.rollback()
    notified = 0

    for order_id in order_ids:
        did_notify = False
        async with session_factory() as order_db:
            async with order_db.begin():
                result = await order_db.execute(
                    select(Order).where(Order.id == order_id)
                    .options(selectinload(Order.invoice), selectinload(Order.customer))
                    .with_for_update().execution_options(populate_existing=True)
                )
                order = result.scalars().first()
                if (not order or not order.invoice or order.fulfillment_overdue_notified_at
                        or not order.fulfillment_date or _as_utc(order.fulfillment_date) > now
                        or order.status not in (OrderStatusEnum.pending, OrderStatusEnum.in_process, OrderStatusEnum.ready)
                        or order.invoice.status not in (InvoiceStatusEnum.partial, InvoiceStatusEnum.paid)):
                    continue
                from app.services.email_outbox_service import (
                    enqueue_order_overdue_for_buyer, enqueue_order_overdue_for_sellers,
                )
                from app.services.notification_service import notify_order_event
                order.fulfillment_overdue_notified_at = now
                await enqueue_order_overdue_for_buyer(order_db, order)
                await enqueue_order_overdue_for_sellers(order_db, order)
                await notify_order_event(order_db, order, "order_overdue")
                did_notify = True
        if did_notify:
            notified += 1
            from app.services.chatbot_notify import notify_chatbot_order_event
            await notify_chatbot_order_event(order_id, "overdue")
    return notified


async def process_order_lifecycle_once(db: AsyncSession) -> dict[str, int]:
    """Expire unpaid checkouts and alert once for paid orders past fulfillment."""
    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    from app.services.payment_service import check_expired_settlements
    expired_settlements = await check_expired_settlements(db)
    return {
        "cancelled_unpaid": await _expire_unpaid_candidates(db, session_factory),
        "overdue_alerts": await _notify_overdue_candidates(db, session_factory),
        "cancelled_settlements": expired_settlements,
    }


async def run_order_lifecycle_worker() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await process_order_lifecycle_once(db)
            if result["cancelled_unpaid"] or result["overdue_alerts"] or result["cancelled_settlements"]:
                logger.info("Order lifecycle worker processed %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Order lifecycle worker iteration failed")
        await asyncio.sleep(settings.order_lifecycle_poll_seconds)
