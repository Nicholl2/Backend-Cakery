from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc
from app.models.faq_item import FaqItem
from app.models.user import User
from typing import Optional, List
from sqlalchemy.orm import selectinload


async def create_faq(
    db: AsyncSession,
    pertanyaan: str,
    jawaban: str,
    created_by: int,
    is_active: bool = True
) -> FaqItem:
    """Create new FAQ item"""
    faq = FaqItem(
        pertanyaan=pertanyaan,
        jawaban=jawaban,
        created_by=created_by,
        is_active=is_active
    )
    db.add(faq)
    await db.flush()
    return faq


async def get_faq_by_id(db: AsyncSession, faq_id: int) -> Optional[FaqItem]:
    """Get FAQ item by ID with creator info"""
    result = await db.execute(
        select(FaqItem)
        .where(FaqItem.id == faq_id)
        .options(selectinload(FaqItem.created_by_user))
    )
    return result.scalars().first()


async def get_all_faqs(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    only_active: bool = False
) -> List[FaqItem]:
    """Get all FAQ items with pagination"""
    query = select(FaqItem).options(selectinload(FaqItem.created_by_user))
    
    if only_active:
        query = query.where(FaqItem.is_active == True)
    
    query = query.order_by(desc(FaqItem.created_at)).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def update_faq(
    db: AsyncSession,
    faq_id: int,
    pertanyaan: Optional[str] = None,
    jawaban: Optional[str] = None,
    is_active: Optional[bool] = None
) -> Optional[FaqItem]:
    """Update FAQ item"""
    update_data = {}
    if pertanyaan is not None:
        update_data["pertanyaan"] = pertanyaan
    if jawaban is not None:
        update_data["jawaban"] = jawaban
    if is_active is not None:
        update_data["is_active"] = is_active

    if update_data:
        await db.execute(
            update(FaqItem).where(FaqItem.id == faq_id).values(**update_data)
        )
        await db.flush()

    return await get_faq_by_id(db, faq_id)


async def delete_faq(db: AsyncSession, faq_id: int) -> bool:
    """Delete FAQ item"""
    result = await db.execute(
        delete(FaqItem).where(FaqItem.id == faq_id)
    )
    return result.rowcount > 0


async def count_faqs(db: AsyncSession) -> int:
    """Count total FAQ items"""
    result = await db.execute(select(FaqItem))
    return len(result.scalars().all())