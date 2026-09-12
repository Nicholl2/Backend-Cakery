from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload
from app.models.user import User
from typing import Optional

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get user by username with role details loaded"""
    stmt = (
        select(User)
        .options(selectinload(User.role))
        .where(User.username == username)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get user by ID with role details loaded"""
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_user_role_level(db: AsyncSession, user_id: int) -> Optional[int]:
    """Get user's role level"""
    stmt = select(User).options(selectinload(User.role)).where(User.id == user_id)
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
    stmt = (
        select(User)
        .options(selectinload(User.role))
        .where(User.is_active == True, User.handles_takeover == True)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Get user by email with role details loaded"""
    stmt = (
        select(User)
        .options(selectinload(User.role))
        .where(User.email == email)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_user_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
    """Get user by phone_number with role details loaded"""
    stmt = (
        select(User)
        .options(selectinload(User.role))
        .where(User.phone_number == phone)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def update_avatar_url(db: AsyncSession, user: User, avatar_url: str) -> User:
    """Update user avatar URL with role eagerly loaded"""
    user.avatar_url = avatar_url
    await db.commit()
    stmt = select(User).options(selectinload(User.role)).where(User.id == user.id)
    result = await db.execute(stmt)
    reloaded_user = result.scalars().first()
    return reloaded_user or user


async def get_all_users(db: AsyncSession, limit: int = 100, offset: int = 0) -> list[User]:
    """Get all internal users ordered by ID with role details loaded"""
    stmt = (
        select(User)
        .options(selectinload(User.role))
        .order_by(User.id.asc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_user(db: AsyncSession, user: User) -> None:
    """Delete user from database"""
    await db.delete(user)
    await db.commit()