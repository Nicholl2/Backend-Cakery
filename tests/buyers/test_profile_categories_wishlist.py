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
        name="Test Buyer",
        email="buyer@example.com",
        phone="08123456789",
        password_hash=hash_password("password123"),
        is_verified=True,
    )
    db_session.add(buyer)
    await db_session.commit()
    await db_session.refresh(buyer)
    return buyer

@pytest.fixture
async def setup_product(db_session: AsyncSession):
    from app.models.product import Product
    prod = Product(nama_produk="Wishlist Prod", harga_jual=10000, is_active=True, is_available=True)
    db_session.add(prod)
    await db_session.commit()
    await db_session.refresh(prod)
    return prod

@pytest.mark.asyncio
async def test_category_crud(db_session: AsyncSession, admin_headers):
    app_cache.clear()
    app.dependency_overrides[get_db] = lambda: db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        # POST /products/categories
        cat_payload = {"name": "Test Cat", "description": "Desc"}
        resp = await ac.post("/products/categories", json=cat_payload, headers=admin_headers)
        assert resp.status_code == 201
        cat_id = resp.json()["id"]

        # GET /products/categories
        resp = await ac.get("/products/categories")
        assert resp.status_code == 200
        assert any(c["id"] == cat_id for c in resp.json())

        # PATCH /products/categories/{cat_id}
        patch_payload = {"name": "Updated Cat"}
        resp = await ac.patch(f"/products/categories/{cat_id}", json=patch_payload, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Cat"

        # DELETE /products/categories/{cat_id}
        resp = await ac.delete(f"/products/categories/{cat_id}", headers=admin_headers)
        assert resp.status_code == 200

    app.dependency_overrides.pop(get_db, None)

@pytest.mark.asyncio
async def test_buyer_wishlist(db_session: AsyncSession, setup_buyer, setup_product):
    app_cache.clear()
    app.dependency_overrides[get_db] = lambda: db_session

    buyer = setup_buyer
    buyer_token_str = create_access_token(user_id=buyer.id, role_level=0, username=buyer.email)
    b_headers = {"Authorization": f"Bearer {buyer_token_str}"}
    
    prod = setup_product
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        # POST add to wishlist
        resp = await ac.post(f"/buyers/me/wishlist/{prod.id}", headers=b_headers)
        assert resp.status_code == 200

        # GET wishlist
        resp = await ac.get("/buyers/me/wishlist", headers=b_headers)
        assert resp.status_code == 200
        assert any(p["id"] == prod.id for p in resp.json())

        # DELETE wishlist
        resp = await ac.delete(f"/buyers/me/wishlist/{prod.id}", headers=b_headers)
        assert resp.status_code == 200

    app.dependency_overrides.pop(get_db, None)

@pytest.mark.asyncio
async def test_buyer_profile_mutation_and_reset(db_session: AsyncSession, setup_buyer):
    app_cache.clear()
    app.dependency_overrides[get_db] = lambda: db_session

    buyer = setup_buyer
    buyer_token_str = create_access_token(user_id=buyer.id, role_level=0, username=buyer.email)
    b_headers = {"Authorization": f"Bearer {buyer_token_str}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
        # GET profile
        resp = await ac.get("/buyers/me", headers=b_headers)
        assert resp.status_code == 200

        # Change password (correct)
        cp_payload = {"current_password": "password123", "new_password": "newpassword123"}
        resp = await ac.post("/buyers/me/change-password", json=cp_payload, headers=b_headers)
        assert resp.status_code == 200

        # Forgot password OTP
        reset_payload = {"email": buyer.email}
        resp = await ac.post("/auth/buyer/forgot-password", json=reset_payload)
        assert resp.status_code == 200

    app.dependency_overrides.pop(get_db, None)
