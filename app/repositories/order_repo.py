from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, cast, String
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum
from app.models.customer import Customer


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
                func.lower(cast(Order.status, String)).notin_(["cancelled", "picked_up"]),
                func.lower(cast(Invoice.status, String)) != "paid",
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
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    return result.scalars().first()


async def get_latest_order_by_wa(db: AsyncSession, nomor_wa: str) -> Optional[Order]:
    result = await db.execute(
        select(Order)
        .join(Customer, Customer.id == Order.customer_id)
        .where(Customer.nomor_wa == nomor_wa)
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.customer),
            selectinload(Order.invoice).selectinload(Invoice.payments),
            selectinload(Order.order_items).selectinload(OrderItem.product),
        )
        .limit(1)
    )
    return result.scalars().first()


async def get_order_by_id(db: AsyncSession, order_id: int) -> Optional[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    return result.scalars().first()


async def update_order_status(db: AsyncSession, order_id: int, status: str) -> Optional[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    order = result.scalars().first()
    if order:
        order.status = status
        await db.flush()
    return order


async def get_orders_by_customer_id(
    db: AsyncSession,
    customer_id: int,
    status: Optional[str] = None,
    created_via: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Order]:
    query = (
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    if status:
        status_val = status.value if hasattr(status, "value") else str(status)
        query = query.where(func.lower(cast(Order.status, String)) == status_val.lower())
    if created_via:
        created_via_val = created_via.value if hasattr(created_via, "value") else str(created_via)
        query = query.where(func.lower(cast(Order.created_via, String)) == created_via_val.lower())
    
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())



# Alias for backward compatibility
get_buyer_orders = get_orders_by_customer_id


async def get_order_by_id_and_customer(db: AsyncSession, order_id: int, customer_id: int) -> Optional[Order]:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.customer_id == customer_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    return result.scalars().first()


async def get_all_orders(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    created_via: Optional[str] = None,
) -> list[Order]:
    query = (
        select(Order)
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.customer),
            selectinload(Order.order_items).selectinload(OrderItem.product),
            selectinload(Order.invoice).selectinload(Invoice.payments),
        )
    )
    if status:
        status_val = status.value if hasattr(status, "value") else str(status)
        query = query.where(func.lower(cast(Order.status, String)) == status_val.lower())
    if created_via:
        created_via_val = created_via.value if hasattr(created_via, "value") else str(created_via)
        query = query.where(func.lower(cast(Order.created_via, String)) == created_via_val.lower())
    
    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


# Alias for backward compatibility
get_seller_orders = get_all_orders


async def get_order_stats(
    db: AsyncSession,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> dict:
    from app.models.payment import Payment
    query = select(Order)
    if start_dt:
        query = query.where(Order.created_at >= start_dt)
    if end_dt:
        query = query.where(Order.created_at <= end_dt)

    orders_res = await db.execute(query)
    orders = orders_res.scalars().all()

    counts = {
        "pending": 0,
        "in_process": 0,
        "ready": 0,
        "delivered": 0,
        "picked_up": 0,
        "cancelled": 0,
        "refunded": 0,
    }
    for o in orders:
        st = o.status.value if hasattr(o.status, "value") else str(o.status)
        st_lower = st.lower()
        if st_lower in counts:
            counts[st_lower] += 1

    order_ids = [
        o.id for o in orders 
        if (o.status.value if hasattr(o.status, "value") else str(o.status)).lower() not in ["cancelled", "refunded"]
    ]
    total_revenue = Decimal("0.00")
    if order_ids:
        rev_q = await db.execute(
            select(func.sum(Payment.jumlah_bayar))
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                func.lower(cast(Payment.payment_status, String)) == "success",
                Invoice.order_id.in_(order_ids),
            )
        )
        total_revenue = rev_q.scalar() or Decimal("0.00")

    return {
        "total_orders": len(orders),
        "pending": counts["pending"],
        "in_process": counts["in_process"],
        "ready": counts["ready"],
        "delivered": counts["delivered"],
        "picked_up": counts["picked_up"],
        "cancelled": counts["cancelled"],
        "refunded": counts["refunded"],
        "active_orders": counts["pending"] + counts["in_process"] + counts["ready"],
        "completed_orders": counts["delivered"] + counts["picked_up"],
        "total_revenue": total_revenue,
    }