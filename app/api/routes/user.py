from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import require_owner
from app.schemas.user import UserTakeoverUpdate, UserTakeoverResponse
from app.services import user_service

router = APIRouter(
    tags=["Users"],
    responses={
        401: {"description": "Unauthorized - missing or invalid token"},
        403: {"description": "Forbidden - only Owner can manage handlers"},
        404: {"description": "User not found"}
    }
)

@router.patch("/{user_id}/takeover-handler", response_model=UserTakeoverResponse)
async def update_takeover_handler(
    user_id: int,
    data: UserTakeoverUpdate,
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_owner)
) -> UserTakeoverResponse:
    """
    Update user's handles_takeover status (Owner only).
    """
    return await user_service.update_takeover_handler(db, user_id, data)
