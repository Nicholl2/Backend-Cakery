from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.product import Category
from app.schemas.product import CategoryCreate, CategoryUpdate
from typing import Optional


async def create(db: AsyncSession, data: CategoryCreate) -> Category:
    cat = Category(**data.model_dump())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def get_by_id(db: AsyncSession, category_id: int) -> Optional[Category]:
    result = await db.execute(select(Category).where(Category.id == category_id))
    return result.scalars().first()


async def get_by_name(db: AsyncSession, name: str) -> Optional[Category]:
    result = await db.execute(select(Category).where(Category.name == name))
    return result.scalars().first()


async def get_all(db: AsyncSession) -> list[Category]:
    result = await db.execute(select(Category).order_by(Category.name))
    return result.scalars().all()


async def update(db: AsyncSession, cat: Category, data: CategoryUpdate) -> Category:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(cat, field, value)
    await db.commit()
    await db.refresh(cat)
    return cat


async def delete(db: AsyncSession, cat: Category) -> bool:
    await db.delete(cat)
    await db.commit()
    return True
