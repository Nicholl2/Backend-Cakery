from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories import customer_repo
from app.schemas.customer import CustomerUpsert, CustomerOut, TakeoverSet, TakeoverStatus


async def get_customer(db: AsyncSession, nomor_wa: str) -> CustomerOut:
    customer = await customer_repo.get_by_nomor_wa(db, nomor_wa)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer tidak ditemukan.")
    return CustomerOut.model_validate(customer)


async def upsert_customer(db: AsyncSession, data: CustomerUpsert) -> tuple[CustomerOut, bool]:
    customer, created = await customer_repo.upsert(
        db, nomor_wa=data.nomor_wa, nama=data.nama, alamat=data.alamat
    )
    await db.commit()
    await db.refresh(customer)
    return CustomerOut.model_validate(customer), created


async def set_takeover(db: AsyncSession, nomor_wa: str, data: TakeoverSet) -> TakeoverStatus:
    if data.active and not data.expires_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="expires_at wajib diisi saat mengaktifkan takeover.",
        )

    customer = await customer_repo.set_takeover(
        db, nomor_wa=nomor_wa, active=data.active, expires_at=data.expires_at
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer tidak ditemukan.")

    await db.commit()
    await db.refresh(customer)
    return _build_takeover_status(customer)


async def get_takeover_status(db: AsyncSession, nomor_wa: str) -> TakeoverStatus:
    customer = await customer_repo.get_by_nomor_wa(db, nomor_wa)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer tidak ditemukan.")
    return _build_takeover_status(customer)


def _build_takeover_status(customer) -> TakeoverStatus:
    now = datetime.now(timezone.utc)
    is_expired = (
        customer.human_takeover_active
        and customer.takeover_expires_at is not None
        and customer.takeover_expires_at.replace(tzinfo=timezone.utc) < now
    )
    return TakeoverStatus(
        nomor_wa=customer.nomor_wa,
        human_takeover_active=customer.human_takeover_active,
        takeover_expires_at=customer.takeover_expires_at,
        is_expired=is_expired,
    )