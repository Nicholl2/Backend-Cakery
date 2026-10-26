import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status

from app.main import app
from app.core.database import get_db
from app.core.security import create_access_token
from app.core.cache import app_cache
from app.models.buyer import Buyer
from app.core.security import hash_password

@pytest.fixture
def admin_token():
    return create_access_token(user_id=1, role_level=2, username="admin")

@pytest.fixture
def buyer_token():
    return create_access_token(user_id=1, role_level=0, username="buyer")

@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}

@pytest.fixture
def buyer_headers(buyer_token):
    return {"Authorization": f"Bearer {buyer_token}"}

@pytest.fixture
async def setup_buyer(db_session: AsyncSession):
    buyer = Buyer(
        name="Test Buyer Admin Delete",
        email="buyer_delete@example.com",
        phone="08123456788",
        password_hash=hash_password("password123"),
        is_verified=True,
    )
    db_session.add(buyer)
    await db_session.commit()
    await db_session.refresh(buyer)
    return buyer

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base

@pytest.fixture
async def db_session():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async_session = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    await test_engine.dispose()

@pytest.mark.asyncio
async def test_admin_buyer_management(db_session: AsyncSession, setup_buyer, admin_headers, buyer_headers):
    app_cache.clear()
    app.dependency_overrides[get_db] = lambda: db_session

    buyer = setup_buyer
    buyer_id = buyer.id
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        # Non-admin / Buyer biasa ditolak saat akses (403 Forbidden). Wait, dependency require_admin_or_owner returns 403.
        resp = await ac.get("/admin/buyers", headers=buyer_headers)
        assert resp.status_code == 403
        
        # Admin gets list
        resp = await ac.get("/admin/buyers", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert any(b["id"] == buyer_id for b in data)

        # Admin gets detail
        resp = await ac.get(f"/admin/buyers/{buyer_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == buyer_id

        # Buyer can't delete
        resp = await ac.delete(f"/admin/buyers/{buyer_id}", headers=buyer_headers)
        assert resp.status_code == 403
        
        # Admin deletes
        resp = await ac.delete(f"/admin/buyers/{buyer_id}", headers=admin_headers)
        assert resp.status_code == 200
        
        # Verify deleted
        resp = await ac.get(f"/admin/buyers/{buyer_id}", headers=admin_headers)
        assert resp.status_code == 404

    app.dependency_overrides.pop(get_db, None)
