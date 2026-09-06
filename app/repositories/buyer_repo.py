from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.buyer import Buyer
from typing import Optional


async def get_buyer_by_id(db: AsyncSession, buyer_id: int) -> Optional[Buyer]:
    """Get buyer by ID"""
    stmt = select(Buyer).where(Buyer.id == buyer_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_buyer_by_email(db: AsyncSession, email: str) -> Optional[Buyer]:
    """Get buyer by email"""
    stmt = select(Buyer).where(Buyer.email == email)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_buyer_by_phone(db: AsyncSession, phone: str) -> Optional[Buyer]:
    """Get buyer by phone number"""
    stmt = select(Buyer).where(Buyer.phone == phone)
    result = await db.execute(stmt)
    return result.scalars().first()


async def create_buyer(
    db: AsyncSession,
    name: str,
    email: str,
    phone: str,
    password_hash: str,
    is_verified: bool = False
) -> Buyer:
    """Create a new buyer record"""
    buyer = Buyer(
        name=name,
        email=email,
        phone=phone,
        password_hash=password_hash,
        is_verified=is_verified
    )
    db.add(buyer)
    await db.commit()
    await db.refresh(buyer)
    return buyer


async def update_buyer_password(db: AsyncSession, buyer: Buyer, new_password_hash: str) -> Buyer:
    """Update buyer password"""
    buyer.password_hash = new_password_hash
    await db.commit()
    await db.refresh(buyer)
    return buyer


async def verify_buyer(db: AsyncSession, buyer: Buyer) -> Buyer:
    """Mark buyer as verified"""
    buyer.is_verified = True
    await db.commit()
    await db.refresh(buyer)
    return buyer


async def update_avatar_url(db: AsyncSession, buyer: Buyer, avatar_url: str) -> Buyer:
    """Update buyer avatar URL"""
    buyer.avatar_url = avatar_url
    await db.commit()
    await db.refresh(buyer)
    return buyer

