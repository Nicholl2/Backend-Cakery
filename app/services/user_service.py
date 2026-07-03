from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories import user_repo
from app.schemas.user import UserTakeoverUpdate, UserTakeoverResponse

async def update_takeover_handler(
    db: AsyncSession,
    user_id: int,
    data: UserTakeoverUpdate
) -> UserTakeoverResponse:
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
    user.handles_takeover = data.handles_takeover
    await db.commit()
    await db.refresh(user)
    return UserTakeoverResponse.model_validate(user)
