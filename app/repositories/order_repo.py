from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum
from app.models.customer import Customer
from typing import Optional


async def create_order(db: AsyncSession, order_obj: Order) -> Order:
    db.add(order_obj)
    await db.flush()
    return order_obj


async def create_order_item(db: AsyncSession, item_obj: OrderItem) -> OrderItem:
    db.add(item_obj)
    await db.flush()
    return item_obj


async def create_invoice(db: AsyncSession, invoice_obj: Invoice) -> Invoice:
    db.add(invoice_obj)
    await db.flush()
    return invoice_obj


async def check_active_unpaid_order(db: AsyncSession, customer_id: int) -> bool:
    """
    True jika customer punya order yang:
    - status bukan 'cancelled' atau 'picked_up', DAN
    - invoice-nya bukan 'paid'
    """
    result = await db.execute(
        select(Order)
        .join(Invoice, Invoice.order_id == Order.id)
        .where(
            and_(
                Order.customer_id == customer_id,
                Order.status.not_in([OrderStatusEnum.cancelled, OrderStatusEnum.picked_up]),
                Invoice.status != InvoiceStatusEnum.paid,
            )
        )
        .limit(1)
    )
    return result.scalars().first() is not None


async def get_order_with_details(db: AsyncSession, order_id: int) -> Optional[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.order_items),
            selectinload(Order.invoice),
        )
    )
    return result.scalars().first()