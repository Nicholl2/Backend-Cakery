from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status, UploadFile
from app.repositories import user_repo
from app.schemas.user import UserTakeoverUpdate, UserTakeoverResponse, UserCreate, UserBootstrap
from app.models.user import User
from app.models.role import Role
from app.core.security import hash_password
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


from sqlalchemy import select, func

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
    if not role_res.scalars().first():
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
    
    try:
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user
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
    await db.refresh(owner_user)
    return owner_user


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

