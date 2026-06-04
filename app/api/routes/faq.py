from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.faq import FaqCreate, FaqUpdate, FaqDetailResponse
from app.services import faq_service
from app.api.dependencies import get_current_user_id, require_admin_or_owner
from typing import List

router = APIRouter(
    prefix="/faq",
    tags=["FAQ Items"],
    responses={
        401: {"description": "Unauthorized - missing or invalid token"},
        403: {"description": "Forbidden - insufficient role permissions"},
        404: {"description": "FAQ item not found"}
    }
)


@router.post("", response_model=FaqDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_faq(
    faq_data: FaqCreate,
    user_id: int = Depends(get_current_user_id),
    role_level: int = Depends(require_admin_or_owner),
    db: AsyncSession = Depends(get_db)
) -> FaqDetailResponse:
    """
    Create new FAQ item (Admin/Owner only).
    
    Only users with role level 1 (Owner) or 2 (Admin) can create FAQ items.
    """
    return await faq_service.create_faq(db, faq_data, created_by=user_id)


@router.get("", response_model=List[FaqDetailResponse], status_code=status.HTTP_200_OK)
async def list_faqs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    only_active: bool = Query(False),
    db: AsyncSession = Depends(get_db)
) -> List[FaqDetailResponse]:
    """
    List all FAQ items (Public endpoint - no authentication required).
    
    - **skip**: Number of items to skip (pagination)
    - **limit**: Maximum items to return (max 1000)
    - **only_active**: Filter to show only active FAQ items
    """
    return await faq_service.list_faqs(db, skip, limit, only_active)


@router.get("/{faq_id}", response_model=FaqDetailResponse, status_code=status.HTTP_200_OK)
async def get_faq(
    faq_id: int,
    db: AsyncSession = Depends(get_db)
) -> FaqDetailResponse:
    """
    Get single FAQ item by ID (Public endpoint - no authentication required).
    """
    return await faq_service.get_faq(db, faq_id)


@router.put("/{faq_id}", response_model=FaqDetailResponse, status_code=status.HTTP_200_OK)
async def update_faq(
    faq_id: int,
    faq_data: FaqUpdate,
    user_id: int = Depends(get_current_user_id),
    role_level: int = Depends(require_admin_or_owner),
    db: AsyncSession = Depends(get_db)
) -> FaqDetailResponse:
    """
    Update FAQ item (Admin/Owner only).
    
    Only users with role level 1 (Owner) or 2 (Admin) can update FAQ items.
    """
    return await faq_service.update_faq(db, faq_id, faq_data)


@router.delete("/{faq_id}", status_code=status.HTTP_200_OK)
async def delete_faq(
    faq_id: int,
    user_id: int = Depends(get_current_user_id),
    role_level: int = Depends(require_admin_or_owner),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Delete FAQ item (Admin/Owner only).
    
    Only users with role level 1 (Owner) or 2 (Admin) can delete FAQ items.
    """
    return await faq_service.delete_faq(db, faq_id)