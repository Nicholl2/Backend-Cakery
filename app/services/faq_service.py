from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories import faq_repo
from app.schemas.faq import FaqCreate, FaqUpdate, FaqResponse, FaqDetailResponse
from typing import List, Optional


async def create_faq(
    db: AsyncSession,
    faq_data: FaqCreate,
    created_by: int
) -> FaqDetailResponse:
    """Create new FAQ item"""
    faq = await faq_repo.create_faq(
        db,
        pertanyaan=faq_data.pertanyaan,
        jawaban=faq_data.jawaban,
        created_by=created_by,
        is_active=faq_data.is_active
    )
    await db.commit()

    # Refresh to load relationship
    await db.refresh(faq)

    return FaqDetailResponse(
        **faq.__dict__,
        created_by_username=faq.created_by_user.username if faq.created_by_user else "Unknown"
    )


async def get_faq(db: AsyncSession, faq_id: int) -> FaqDetailResponse:
    """Get single FAQ item"""
    faq = await faq_repo.get_faq_by_id(db, faq_id)

    if not faq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FAQ item not found"
        )

    return FaqDetailResponse(
        **faq.__dict__,
        created_by_username=faq.created_by_user.username if faq.created_by_user else "Unknown"
    )


async def list_faqs(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    only_active: bool = False
) -> List[FaqDetailResponse]:
    """List all FAQ items"""
    faqs = await faq_repo.get_all_faqs(
        db,
        skip=skip,
        limit=limit,
        only_active=only_active
    )

    return [
        FaqDetailResponse(
            **faq.__dict__,
            created_by_username=faq.created_by_user.username if faq.created_by_user else "Unknown"
        )
        for faq in faqs
    ]


async def update_faq(
    db: AsyncSession,
    faq_id: int,
    faq_data: FaqUpdate
) -> FaqDetailResponse:
    """Update FAQ item"""
    # Check if FAQ exists
    existing_faq = await faq_repo.get_faq_by_id(db, faq_id)
    if not existing_faq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FAQ item not found"
        )

    # Update FAQ
    updated_faq = await faq_repo.update_faq(
        db,
        faq_id,
        pertanyaan=faq_data.pertanyaan,
        jawaban=faq_data.jawaban,
        is_active=faq_data.is_active
    )

    await db.commit()

    return FaqDetailResponse(
        **updated_faq.__dict__,
        created_by_username=updated_faq.created_by_user.username if updated_faq.created_by_user else "Unknown"
    )


async def delete_faq(db: AsyncSession, faq_id: int) -> dict:
    """Delete FAQ item"""
    # Check if FAQ exists
    existing_faq = await faq_repo.get_faq_by_id(db, faq_id)
    if not existing_faq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FAQ item not found"
        )

    # Delete FAQ
    success = await faq_repo.delete_faq(db, faq_id)
    await db.commit()

    if not success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to delete FAQ item"
        )

    return {"message": "FAQ item deleted successfully", "faq_id": faq_id}