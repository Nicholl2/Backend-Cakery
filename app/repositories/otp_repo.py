from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.otp_code import OTPCode
from typing import Optional
from datetime import datetime


async def create_otp(
    db: AsyncSession,
    otp_id: str,
    target: str,
    channel: str,
    purpose: str,
    code_hash: str,
    expires_at: datetime
) -> OTPCode:
    """Create a new OTP code record"""
    otp = OTPCode(
        id=otp_id,
        target=target,
        channel=channel,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=expires_at,
        is_used=False
    )
    db.add(otp)
    await db.commit()
    await db.refresh(otp)
    return otp


async def get_otp_by_id(db: AsyncSession, otp_id: str) -> Optional[OTPCode]:
    """Get OTP code record by ID"""
    stmt = select(OTPCode).where(OTPCode.id == otp_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def mark_otp_used(db: AsyncSession, otp: OTPCode) -> OTPCode:
    """Mark OTP code as used"""
    otp.is_used = True
    await db.commit()
    await db.refresh(otp)
    return otp
