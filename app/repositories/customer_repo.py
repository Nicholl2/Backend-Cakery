from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.customer import Customer
from typing import Optional
from datetime import datetime


async def get_by_nomor_wa(db: AsyncSession, nomor_wa: str) -> Optional[Customer]:
    result = await db.execute(select(Customer).where(Customer.nomor_wa == nomor_wa))
    return result.scalars().first()


async def upsert(
    db: AsyncSession,
    nomor_wa: str,
    nama: str,
    alamat: Optional[str] = None,
) -> tuple[Customer, bool]:
    """
    Returns (customer, created).
    created=True berarti baru dibuat, False berarti update.
    """
    customer = await get_by_nomor_wa(db, nomor_wa)
    if customer:
        customer.nama = nama
        if alamat is not None:
            customer.alamat = alamat
        await db.flush()
        return customer, False

    customer = Customer(nama=nama, nomor_wa=nomor_wa, alamat=alamat)
    db.add(customer)
    await db.flush()
    return customer, True


async def set_takeover(
    db: AsyncSession,
    nomor_wa: str,
    active: bool,
    expires_at: Optional[datetime] = None,
) -> Optional[Customer]:
    customer = await get_by_nomor_wa(db, nomor_wa)
    if not customer:
        return None
    customer.human_takeover_active = active
    customer.takeover_expires_at = expires_at if active else None
    await db.flush()
    return customer