from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import require_owner, get_current_user_id
from app.schemas.user import UserTakeoverUpdate, UserTakeoverResponse, UserCreate, UserOut
from app.services import user_service

router = APIRouter(
    tags=["Users"],
    responses={
        401: {"description": "Unauthorized - missing or invalid token"},
        403: {"description": "Forbidden - only Owner can manage handlers"},
        404: {"description": "User not found"},
        400: {"description": "Bad Request - Invalid image format or size exceeds 5MB"},
        500: {"description": "Internal Server Error / Cloudinary configuration missing"},
        502: {"description": "Bad Gateway - Cloudinary upload failed"},
    }
)

@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_owner)
) -> UserOut:
    """
    Create a new internal user (Admin/Staff/Owner) - Owner only.
    """
    user = await user_service.create_user(db, data)
    return UserOut.model_validate(user)

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

@router.post("/me/avatar", response_model=UserOut, status_code=status.HTTP_200_OK,
             summary="Upload foto avatar User internal ke Cloudinary")
async def upload_user_avatar(
    file: UploadFile = File(..., description="File gambar avatar (JPEG/PNG/WEBP, maks 5MB)"),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Upload foto avatar akun user internal langsung di-stream ke folder Cloudinary `toti-cakery/avatars/`.
    Menyimpan secure HTTP URL (`secure_url`) ke database dan mengembalikan data profil user terbaru.
    """
    user = await user_service.upload_user_avatar(db, user_id, file)
    return UserOut.model_validate(user)

