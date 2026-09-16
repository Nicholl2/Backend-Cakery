from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.customer import Customer
from typing import Optional
from datetime import datetime


from app.utils.phone import get_phone_variants, normalize_phone
from sqlalchemy.exc import IntegrityError


async def get_by_nomor_wa(db: AsyncSession, nomor_wa: str) -> Optional[Customer]:
    """Find customer by nomor_wa, falling back to all common formatting variants (08xx, 62xx, +62xx)."""
    if not nomor_wa:
        return None
    # 1. Check exact match
    result = await db.execute(select(Customer).where(Customer.nomor_wa == nomor_wa))
    customer = result.scalars().first()
    if customer:
        return customer

    # 2. Check candidate variants
    variants = get_phone_variants(nomor_wa)
    if variants:
        stmt = select(Customer).where(Customer.nomor_wa.in_(variants)).limit(1)
        res = await db.execute(stmt)
        return res.scalars().first()
    return None


async def upsert(
    db: AsyncSession,
    nomor_wa: str,
    nama: str,
    alamat: Optional[str] = None,
) -> tuple[Customer, bool]:
    """
    Returns (customer, created).
    created=True berarti baru dibuat, False berarti update.
    Uses get_or_create logic with phone variant matching to prevent 409 / unique constraint conflicts.
    """
    customer = await get_by_nomor_wa(db, nomor_wa)
    if customer:
        customer.nama = nama
        if alamat is not None:
            customer.alamat = alamat
        await db.flush()
        return customer, False

    clean_wa = normalize_phone(nomor_wa, as_http_exception=False) or nomor_wa
    customer = Customer(nama=nama, nomor_wa=clean_wa, alamat=alamat)
    db.add(customer)
    try:
        await db.flush()
        return customer, True
    except IntegrityError:
        # Race condition or alternate formatting already created in DB: fetch existing
        customer = await get_by_nomor_wa(db, nomor_wa)
        if customer:
            customer.nama = nama
            if alamat is not None:
                customer.alamat = alamat
            await db.flush()
            return customer, False
        raise


async def set_takeover(
    db: AsyncSession,
    nomor_wa: str,
    active: bool,
    expires_at: Optional[datetime] = None,
) -> tuple[Customer, bool]:
    """
    UPSERT takeover: jika customer belum ada, buat record baru.
    Returns (customer, created) — created=True jika customer baru dibuat.
    """
    customer = await get_by_nomor_wa(db, nomor_wa)
    created = False
    if not customer:
        customer = Customer(nama="Customer", nomor_wa=nomor_wa)
        db.add(customer)
        await db.flush()
        created = True
    customer.human_takeover_active = active
    customer.takeover_expires_at = expires_at if active else None
    await db.flush()
    return customer, created