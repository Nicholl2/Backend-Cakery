import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401 - register all mapped tables
from app.core.config import settings
from app.core.database import Base, seed_initial_data
from app.core.security import verify_password
from app.models.product import Product
from app.models.role import Role
from app.models.user import User


@pytest.fixture
async def production_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def configure_production_bootstrap(monkeypatch, *, password="long-private-password"):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "bootstrap_owner_username", "first-owner")
    monkeypatch.setattr(settings, "bootstrap_owner_email", "owner@example.test")
    monkeypatch.setattr(
        settings, "bootstrap_owner_password", SecretStr(password)
    )
    monkeypatch.setattr(settings, "bootstrap_owner_phone_number", "")


@pytest.mark.asyncio
async def test_production_seed_requires_private_owner_credentials(
    production_db, monkeypatch
):
    configure_production_bootstrap(monkeypatch, password="")

    with pytest.raises(RuntimeError, match="BOOTSTRAP_OWNER_PASSWORD"):
        await seed_initial_data(production_db)

    assert await production_db.scalar(select(func.count()).select_from(Role)) == 0
    assert await production_db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_production_seed_creates_only_configured_owner_once(
    production_db, monkeypatch
):
    configure_production_bootstrap(monkeypatch)

    await seed_initial_data(production_db)
    owner = await production_db.scalar(
        select(User).where(User.username == "first-owner")
    )
    assert owner is not None
    assert owner.email == "owner@example.test"
    assert owner.role.level == 1
    assert verify_password("long-private-password", owner.password_hash)
    assert await production_db.scalar(select(func.count()).select_from(User)) == 1
    assert await production_db.scalar(select(func.count()).select_from(Product)) == 0

    original_hash = owner.password_hash
    configure_production_bootstrap(monkeypatch, password="a-new-private-password")
    await seed_initial_data(production_db)

    await production_db.refresh(owner)
    assert owner.password_hash == original_hash
