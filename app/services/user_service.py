from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status, UploadFile
from app.repositories import user_repo
from app.schemas.user import (
    UserTakeoverUpdate,
    UserTakeoverResponse,
    UserCreate,
    UserBootstrap,
    UserProfileUpdate,
    ChangePasswordRequest,
    UserAdminUpdate,
)
from app.models.user import User
from app.models.role import Role
from app.core.security import hash_password, verify_password
from app.utils.cloudinary_helper import upload_image_to_cloudinary
from app.utils.phone import normalize_phone

async def get_owner_wa_numbers(db: AsyncSession) -> list[str]:
    stmt = select(User).where(
        User.role_id == 1,
        User.is_active == True,
        User.phone_number.isnot(None),
        User.phone_number != ""
    )
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    numbers = set()
    for u in users:
        try:
            norm_num = normalize_phone(u.phone_number, as_http_exception=False)
            if norm_num:
                numbers.add(norm_num)
        except ValueError:
            pass
            
    return list(numbers)

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


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    # 1. Map role to role_id jika role_id tidak dikirim tapi role dikirim
    if data.role_id is None:
        if data.role:
            role_input = data.role.strip().lower()
            role_stmt = select(Role).where(func.lower(Role.nama_role) == role_input)
            role_res = await db.execute(role_stmt)
            role_obj = role_res.scalars().first()
            if not role_obj:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Role '{data.role}' tidak ditemukan. Role yang valid: admin, staff, owner."
                )
            data.role_id = role_obj.id
        else:
            raise HTTPException(status_code=400, detail="role_id atau role wajib diisi")

    # 2. Cek username
    existing_user = await user_repo.get_user_by_username(db, data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username sudah terdaftar"
        )
        
    # 3. Cek email & phone
    if data.email:
        existing_email = await db.execute(select(User).where(User.email == data.email))
        if existing_email.scalars().first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email sudah terdaftar")
            
    if data.phone_number:
        existing_phone = await db.execute(select(User).where(User.phone_number == data.phone_number))
        if existing_phone.scalars().first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nomor telepon sudah terdaftar")

    # 4. Cek role eksistensi di DB
    role_stmt = select(Role).where(Role.id == data.role_id)
    role_res = await db.execute(role_stmt)
    role_obj = role_res.scalars().first()
    if not role_obj:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role ID {data.role_id} tidak valid/tidak ditemukan"
        )

    # 5. Hash password & Insert
    hashed_pwd = hash_password(data.password)
    
    new_user = User(
        username=data.username,
        password_hash=hashed_pwd,
        role_id=data.role_id,
        nomor_wa_admin=data.nomor_wa_admin or data.phone_number, # Fallback to phone_number if not provided
        handles_takeover=data.handles_takeover if data.handles_takeover is not None else False,
        is_active=data.is_active if data.is_active is not None else True,
        email=data.email,
        phone_number=data.phone_number,
    )
    new_user.role = role_obj
    
    try:
        db.add(new_user)
        await db.commit()
        stmt = select(User).options(selectinload(User.role)).where(User.id == new_user.id)
        result = await db.execute(stmt)
        user = result.scalars().first()
        return user or new_user
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan user: {str(e)}")


async def ensure_roles_exist(db: AsyncSession) -> None:
    # Ensure standard roles exist: 1: Owner, 2: Admin, 3: Staff
    result = await db.execute(select(Role))
    roles = result.scalars().all()
    if not roles:
        owner_role = Role(id=1, nama_role="Owner", level=1)
        admin_role = Role(id=2, nama_role="Admin", level=2)
        staff_role = Role(id=3, nama_role="Staff", level=3)
        db.add_all([owner_role, admin_role, staff_role])
        await db.commit()


async def bootstrap_owner(db: AsyncSession, data: UserBootstrap) -> User:
    # Ensure default roles exist
    await ensure_roles_exist(db)
    
    # Check if user list is empty
    users_result = await db.execute(select(User))
    if users_result.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tabel users tidak kosong. Bootstrap hanya diizinkan untuk setup pertama kali."
        )
        
    # Hash password
    hashed_pwd = hash_password(data.password)
    
    owner_user = User(
        username=data.username,
        password_hash=hashed_pwd,
        role_id=1,  # Owner role has ID 1
        nomor_wa_admin=data.nomor_wa_admin,
        handles_takeover=True,
        is_active=True,
        email=getattr(data, "email", None),
        phone_number=getattr(data, "phone_number", None),
    )
    db.add(owner_user)
    await db.commit()
    stmt = select(User).options(selectinload(User.role)).where(User.id == owner_user.id)
    result = await db.execute(stmt)
    return result.scalars().first() or owner_user


async def upload_user_avatar(db: AsyncSession, user_id: int, file: UploadFile) -> User:
    """Upload and update internal user avatar image to Cloudinary (folder: toti-cakery/avatars)."""
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
    secure_url = await upload_image_to_cloudinary(file, folder="toti-cakery/avatars")
    updated_user = await user_repo.update_avatar_url(db, user, secure_url)
    return updated_user


async def get_user_profile(db: AsyncSession, user_id: int) -> User:
    """Get profile of currently logged-in internal user."""
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
    return user


async def update_user_profile(db: AsyncSession, user_id: int, data: UserProfileUpdate) -> User:
    """Update profile of currently logged-in internal user."""
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )

    # Validasi keunikan username jika diubah
    if data.username and data.username != user.username:
        existing = await user_repo.get_user_by_username(db, data.username)
        if existing and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username sudah digunakan"
            )
        user.username = data.username

    # Validasi keunikan email jika diubah
    if data.email and data.email != user.email:
        existing = await user_repo.get_user_by_email(db, data.email)
        if existing and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email sudah digunakan"
            )
        user.email = data.email

    # Validasi keunikan nomor telepon jika diubah
    if data.phone_number and data.phone_number != user.phone_number:
        existing = await user_repo.get_user_by_phone(db, data.phone_number)
        if existing and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nomor telepon sudah digunakan"
            )
        user.phone_number = data.phone_number

    if data.nomor_wa_admin is not None:
        user.nomor_wa_admin = data.nomor_wa_admin

    await db.commit()
    stmt = select(User).options(selectinload(User.role)).where(User.id == user.id)
    res = await db.execute(stmt)
    return res.scalars().first() or user


async def change_user_password(db: AsyncSession, user_id: int, data: ChangePasswordRequest) -> None:
    """Change password for currently logged-in user with old password verification."""
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )

    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password lama tidak sesuai"
        )

    user.password_hash = hash_password(data.new_password)
    await db.commit()


async def get_all_internal_users(db: AsyncSession, limit: int = 100, offset: int = 0) -> list[User]:
    """Get all internal users list (Owner only)."""
    return await user_repo.get_all_users(db, limit=limit, offset=offset)


async def admin_update_user(
    db: AsyncSession,
    target_user_id: int,
    data: UserAdminUpdate,
    current_user_id: int
) -> User:
    """Owner edits internal user details (role, status, credentials, etc.)."""
    user = await user_repo.get_user_by_id(db, target_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )

    # Validasi keunikan username jika diubah
    if data.username and data.username != user.username:
        existing = await user_repo.get_user_by_username(db, data.username)
        if existing and existing.id != target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username sudah digunakan"
            )
        user.username = data.username

    # Validasi keunikan email jika diubah
    if data.email and data.email != user.email:
        existing = await user_repo.get_user_by_email(db, data.email)
        if existing and existing.id != target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email sudah digunakan"
            )
        user.email = data.email

    # Validasi keunikan nomor telepon jika diubah
    if data.phone_number and data.phone_number != user.phone_number:
        existing = await user_repo.get_user_by_phone(db, data.phone_number)
        if existing and existing.id != target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nomor telepon sudah digunakan"
            )
        user.phone_number = data.phone_number

    if data.nomor_wa_admin is not None:
        user.nomor_wa_admin = data.nomor_wa_admin

    # Update role jika disediakan
    if data.role_id is not None:
        role_res = await db.execute(select(Role).where(Role.id == data.role_id))
        role_obj = role_res.scalars().first()
        if not role_obj:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Role ID {data.role_id} tidak valid"
            )
        user.role_id = data.role_id
        user.role = role_obj
    elif data.role is not None:
        role_input = data.role.strip().lower()
        role_res = await db.execute(select(Role).where(func.lower(Role.nama_role) == role_input))
        role_obj = role_res.scalars().first()
        if not role_obj:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Role '{data.role}' tidak valid. Pilihan: owner, admin, staff"
            )
        user.role_id = role_obj.id
        user.role = role_obj

    if data.handles_takeover is not None:
        user.handles_takeover = data.handles_takeover

    if data.is_active is not None:
        if target_user_id == current_user_id and not data.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tidak dapat menonaktifkan akun sendiri"
            )
        user.is_active = data.is_active

    if data.password:
        user.password_hash = hash_password(data.password)

    await db.commit()
    stmt = select(User).options(selectinload(User.role)).where(User.id == user.id)
    res = await db.execute(stmt)
    return res.scalars().first() or user


async def deactivate_user(db: AsyncSession, target_user_id: int, current_user_id: int) -> User:
    """Deactivate user account (Owner only)."""
    if target_user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak dapat menonaktifkan akun sendiri"
        )
    user = await user_repo.get_user_by_id(db, target_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
    user.is_active = False
    await db.commit()
    stmt = select(User).options(selectinload(User.role)).where(User.id == user.id)
    res = await db.execute(stmt)
    return res.scalars().first() or user


async def delete_user(db: AsyncSession, target_user_id: int, current_user_id: int) -> dict:
    """Delete or soft-deactivate user account (Owner only)."""
    if target_user_id == current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak dapat menghapus akun sendiri"
        )
    user = await user_repo.get_user_by_id(db, target_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User tidak ditemukan"
        )
    try:
        await user_repo.delete_user(db, user)
        return {"message": "User berhasil dihapus", "id": target_user_id}
    except Exception:
        await db.rollback()
        # Fallback to soft-deactivate if related records exist (FK constraints)
        user.is_active = False
        await db.commit()
        return {"message": "User dinonaktifkan karena memiliki riwayat relasi data", "id": target_user_id}


