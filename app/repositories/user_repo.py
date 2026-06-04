from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User
from app.models.role import Role
from typing import Optional


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    """Get user by username with role details"""
    result = await db.execute(
        select(User).where(User.username == username).options(
            select(User).options()  # Eager load role
        )
    )
    return result.scalars().first()


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """Get user by ID with role details"""
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    return result.scalars().first()


async def get_user_role_level(db: AsyncSession, user_id: int) -> Optional[int]:
    """Get user's role level"""
    user = await get_user_by_id(db, user_id)
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