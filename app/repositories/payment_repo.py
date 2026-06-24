from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.payment import Payment
from app.models.order import Invoice
from typing import Optional, List

async def create_payment(db: AsyncSession, payment_obj: Payment) -> Payment:
    db.add(payment_obj)
    await db.flush()
    return payment_obj

async def get_payment_by_pg_id(db: AsyncSession, pg_transaction_id: str) -> Optional[Payment]:
    result = await db.execute(
        select(Payment).where(Payment.pg_transaction_id == pg_transaction_id)
    )
    return result.scalars().first()

async def get_payments_by_order_id(db: AsyncSession, order_id: int) -> List[Payment]:
    result = await db.execute(
        select(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .where(Invoice.order_id == order_id)
    )
    return list(result.scalars().all())
