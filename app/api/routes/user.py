from typing import Optional
from fastapi import APIRouter, Depends, File, UploadFile, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import require_owner, get_current_user_id, require_service_key
from app.schemas.user import (
    UserTakeoverUpdate,
    UserTakeoverResponse,
    UserCreate,
    UserOut,
    UserProfileUpdate,
    ChangePasswordRequest,
    UserAdminUpdate,
)
from app.services import user_service

router = APIRouter(
    tags=["Users"],
    responses={
        401: {"description": "Unauthorized - missing or invalid token"},
        403: {"description": "Forbidden - Insufficient role permissions"},
        404: {"description": "User not found"},
        400: {"description": "Bad Request - Validation or conflict error"},
        500: {"description": "Internal Server Error"},
    }
)


# ── CURRENT USER (ME) PROFILE & SETTINGS ────────────────────────────────────

@router.get("/me", response_model=UserOut, status_code=status.HTTP_200_OK,
            summary="Ambil profil user internal/seller yang sedang login")
async def get_my_profile(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Mengambil data profil lengkap untuk user internal/seller yang sedang login berdasarkan token JWT.
    """
    user = await user_service.get_user_profile(db, user_id)
    return UserOut.model_validate(user)


@router.put("/me", response_model=UserOut, status_code=status.HTTP_200_OK,
            summary="Update data profil user yang sedang login (PUT)")
@router.patch("/me", response_model=UserOut, status_code=status.HTTP_200_OK,
              summary="Update data profil user yang sedang login (PATCH)")
async def update_my_profile(
    data: UserProfileUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Update data profil user yang sedang login (username, email, phone_number, nomor_wa_admin).
    """
    user = await user_service.update_user_profile(db, user_id, data)
    return UserOut.model_validate(user)


@router.post("/me/change-password", status_code=status.HTTP_200_OK,
             summary="Ubah password user yang sedang login")
async def change_my_password(
    data: ChangePasswordRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Mengubah password user yang sedang login dengan memverifikasi password lama terlebih dahulu.
    """
    await user_service.change_user_password(db, user_id, data)
    return {"status": "success", "message": "Password berhasil diubah"}


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


# ── OWNER USER MANAGEMENT (OWNER ONLY - ADMIN/STAFF GET 403) ────────────────

@router.get("", response_model=list[UserOut], status_code=status.HTTP_200_OK,
            dependencies=[Depends(require_owner)],
            summary="List seluruh akun seller/internal (Owner Only)")
async def list_all_users(
    limit: int = Query(100, ge=1, le=500, description="Jumlah data per halaman"),
    offset: int = Query(0, ge=0, description="Offset pagination"),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    """
    Mengambil daftar seluruh akun seller/internal (Owner, Admin, Staff).
    Khusus untuk role Owner (Level 1). Admin dan Staff ditolak (403 Forbidden).
    """
    users = await user_service.get_all_internal_users(db, limit=limit, offset=offset)
    return [UserOut.model_validate(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_owner)],
             summary="Daftarkan akun internal baru (Owner Only)")
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Create a new internal user (Admin/Staff/Owner) - Owner only.
    """
    user = await user_service.create_user(db, data)
    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut, status_code=status.HTTP_200_OK,
            dependencies=[Depends(require_owner)],
            summary="Edit data user internal lain oleh Owner (PUT)")
@router.patch("/{user_id}", response_model=UserOut, status_code=status.HTTP_200_OK,
              dependencies=[Depends(require_owner)],
              summary="Edit data user internal lain oleh Owner (PATCH)")
async def update_user(
    user_id: int,
    data: UserAdminUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Edit data akun user internal lain oleh Owner (role, handles_takeover, status aktif, reset password, dll.).
    Owner tidak diizinkan menonaktifkan akunnya sendiri via endpoint ini.
    Admin dan Staff ditolak (403 Forbidden).
    """
    user = await user_service.admin_update_user(db, user_id, data, current_user_id)
    return UserOut.model_validate(user)


@router.patch("/{user_id}/deactivate", response_model=UserOut, status_code=status.HTTP_200_OK,
              dependencies=[Depends(require_owner)],
              summary="Deaktivasi akun user internal oleh Owner")
async def deactivate_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Menonaktifkan akun user internal (`is_active = False`) oleh Owner.
    Owner tidak diizinkan menonaktifkan akunnya sendiri.
    Admin dan Staff ditolak (403 Forbidden).
    """
    user = await user_service.deactivate_user(db, user_id, current_user_id)
    return UserOut.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_200_OK,
               dependencies=[Depends(require_owner)],
               summary="Hapus atau deaktivasi akun user internal oleh Owner")
async def delete_user(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Menghapus akun user internal (atau deaktivasi jika ada relasi foreign key transaksi) oleh Owner.
    Owner tidak diizinkan menghapus akunnya sendiri.
    Admin dan Staff ditolak (403 Forbidden).
    """
    return await user_service.delete_user(db, user_id, current_user_id)


@router.patch("/{user_id}/takeover-handler", response_model=UserTakeoverResponse,
              dependencies=[Depends(require_owner)],
              summary="Update status live takeover handler (Owner Only)")
async def update_takeover_handler(
    user_id: int,
    data: UserTakeoverUpdate,
    db: AsyncSession = Depends(get_db),
) -> UserTakeoverResponse:
    """
    Update user's handles_takeover status (Owner only).
    """
    return await user_service.update_takeover_handler(db, user_id, data)


# ── SERVICE-TO-SERVICE CHATBOT ENDPOINTS ────────────────────────────────────

@router.get("/owner-numbers", dependencies=[Depends(require_service_key)],
            summary="Ambil nomor WA Owner aktif untuk chatbot service")
async def get_owner_numbers(db: AsyncSession = Depends(get_db)):
    """
    Get all active Owner's WhatsApp numbers for Chatbot service.
    """
    numbers = await user_service.get_owner_wa_numbers(db)
    return {"numbers": numbers}
