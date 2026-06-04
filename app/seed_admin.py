import asyncio

from sqlalchemy import select

from app import models  # noqa: F401 - ensure all models are registered
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User


DEFAULT_USERNAME = "A Meng"
DEFAULT_PASSWORD = "Admin_123"


async def ensure_role(db, nama_role: str, level: int) -> Role:
    result = await db.execute(select(Role).where(Role.level == level))
    role = result.scalars().first()

    if role:
        role.nama_role = nama_role
        return role

    role = Role(nama_role=nama_role, level=level)
    db.add(role)
    await db.flush()
    return role


async def seed_admin() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        await ensure_role(db, "Owner", 1)
        admin_role = await ensure_role(db, "Admin", 2)
        await ensure_role(db, "Staff", 3)

        result = await db.execute(select(User).where(User.username == DEFAULT_USERNAME))
        user = result.scalars().first()

        if user:
            user.password_hash = hash_password(DEFAULT_PASSWORD)
            user.role_id = admin_role.id
            user.is_active = True
        else:
            db.add(
                User(
                    username=DEFAULT_USERNAME,
                    password_hash=hash_password(DEFAULT_PASSWORD),
                    role_id=admin_role.id,
                    is_active=True,
                )
            )

        await db.commit()

    print(f"Admin user ready: username={DEFAULT_USERNAME!r}, password={DEFAULT_PASSWORD!r}")


if __name__ == "__main__":
    asyncio.run(seed_admin())
