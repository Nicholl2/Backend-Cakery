from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload # WAJIB IMPORT INI
from app.models.user import User
from typing import Optional

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get user by username with role details loaded"""
    stmt = (
        select(User)
        .options(joinedload(User.role)) # Memuat relasi role dengan benar
        .where(User.username == username)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get user by ID"""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_user_role_level(db: AsyncSession, user_id: int) -> Optional[int]:
    """Get user's role level"""
    # Karena kita sudah tahu user_id, kita bisa query user dengan role-nya
    stmt = select(User).options(joinedload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    if user and user.role:
        return user.role.level
    return None

async def is_user_active(db: AsyncSession, user_id: int) -> bool:
    """Check if user is active"""
    result = await db.execute(
        select(User.is_active).where(User.id == user_id)
    )
    is_active = result.scalar()
    return is_active if is_active is not None else False


async def get_takeover_handlers(db: AsyncSession) -> list[User]:
    """Get active users who handle takeover"""
    stmt = select(User).where(User.is_active == True, User.handles_takeover == True)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Get user by email with role details loaded"""
    stmt = (
        select(User)
        .options(joinedload(User.role))
        .where(User.email == email)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_user_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
    """Get user by phone_number with role details loaded"""
    stmt = (
        select(User)
        .options(joinedload(User.role))
        .where(User.phone_number == phone)
    )
    result = await db.execute(stmt)
    return result.scalars().first()