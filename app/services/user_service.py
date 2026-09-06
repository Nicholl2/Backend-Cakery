from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from app.repositories import user_repo
from app.schemas.user import UserTakeoverUpdate, UserTakeoverResponse, UserCreate, UserBootstrap
from app.models.user import User
from app.models.role import Role
from app.core.security import hash_password

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
    # Cek username
    existing = await user_repo.get_user_by_username(db, data.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username sudah terdaftar"
        )
        
    # Cek role
    role_stmt = select(Role).where(Role.id == data.role_id)
    role_res = await db.execute(role_stmt)
    if not role_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role ID {data.role_id} tidak valid/tidak ditemukan"
        )

    # Hash password
    hashed_pwd = hash_password(data.password)
    
    new_user = User(
        username=data.username,
        password_hash=hashed_pwd,
        role_id=data.role_id,
        nomor_wa_admin=data.nomor_wa_admin,
        handles_takeover=data.handles_takeover if data.handles_takeover is not None else False,
        is_active=data.is_active if data.is_active is not None else True,
        email=data.email,
        phone_number=data.phone_number,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


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
