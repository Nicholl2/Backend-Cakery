import json
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.order import Order
from app.models.user import User
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.repositories import notification_repo

logger = logging.getLogger(__name__)


async def notify_order_created(db: AsyncSession, order: Order, actor_buyer: Optional[Buyer] = None):
    """
    Generated in the order transaction for customer-originated orders.
    Recipients: active internal Seller users (Staff/Admin/Owner).
    """
    # Find active internal seller users. The caller owns the transaction so these
    # notifications commit atomically with order creation.
    result = await db.execute(
        select(User).where(User.is_active.is_(True))
    )
    seller_users = result.scalars().all()

    order_number = f"ORDER-{order.id}"
    metadata = json.dumps({
        "order_id": order.id,
        "order_number": order_number,
        "total_price": float(order.total_harga_pesanan) if order.total_harga_pesanan else 0,
    })

    actor_id = actor_buyer.id if actor_buyer else None
    actor_type = "buyer" if actor_buyer else "system"

    for user in seller_users:
        await notification_repo.create_notification(
            db=db,
            type="order_created",
            recipient_user_id=user.id,
            order_id=order.id,
            actor_type=actor_type,
            actor_id=actor_id,
            metadata_json=metadata,
        )


async def notify_order_status_updated(
    db: AsyncSession,
    order: Order,
    old_status: str,
    new_status: str,
    actor_user: Optional[User] = None
):
    """
    Generated when Seller changes order status.
    Recipient: Buyer associated with that order.
    """
    if old_status == new_status:
        return  # Prevent duplicate notification for identical status update

    # Resolve the Buyer account and add the notification to the caller's order
    # transaction. If persistence fails, the status update must roll back too.
    recipient_buyer_id = None
    if order.customer_id:
        customer_res = await db.execute(
            select(Customer).where(Customer.id == order.customer_id)
        )
        customer = customer_res.scalars().first()
        if customer and customer.nomor_wa:
            buyer_res = await db.execute(
                select(Buyer).where(Buyer.phone == customer.nomor_wa)
            )
            buyer = buyer_res.scalars().first()
            if buyer:
                recipient_buyer_id = buyer.id

    if not recipient_buyer_id:
        logger.info(f"[NOTIFICATION_INFO] No registered Buyer account found for order {order.id} customer.")
        return

    order_number = f"ORDER-{order.id}"
    metadata = json.dumps({
        "order_id": order.id,
        "order_number": order_number,
        "status": new_status,
        "old_status": old_status,
    })
    actor_id = actor_user.id if actor_user else None
    await notification_repo.create_notification(
        db=db,
        type="order_status_updated",
        recipient_buyer_id=recipient_buyer_id,
        order_id=order.id,
        actor_type="user",
        actor_id=actor_id,
        metadata_json=metadata,
    )
