import pytest
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import (
    create_access_token,
    decode_token,
    revoke_token,
    is_token_blacklisted,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.core.cache import app_cache
from app.main import app


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
async def test_access_token_expire_minutes_configuration():
    """Verify that ACCESS_TOKEN_EXPIRE_MINUTES is set to 60 (1 hour)."""
    assert settings.access_token_expire_minutes == 60
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 60
    assert ACCESS_TOKEN_EXPIRE_MINUTES == 60


@pytest.mark.asyncio
async def test_token_creation_includes_jti_and_60m_expiry():
    """Verify create_access_token creates a JWT with JTI and ~60 minutes expiry."""
    token = create_access_token(user_id=1, role_level=1, username="owner_test")
    payload = decode_token(token)

    assert "jti" in payload
    assert payload["sub"] == "1"
    assert payload["username"] == "owner_test"

    exp = payload["exp"]
    now_ts = datetime.now(timezone.utc).timestamp()
    # Expiration should be roughly 60 minutes (between 58 and 61 minutes from now)
    diff_minutes = (exp - now_ts) / 60
    assert 58 <= diff_minutes <= 61


@pytest.mark.asyncio
async def test_logout_endpoint_and_token_revocation(db_session: AsyncSession):
    """
    Test that:
    1. POST /auth/logout with valid token returns 200 OK.
    2. Revoked token is blacklisted in app_cache.
    3. Calling decode_token or protected endpoints with the revoked token raises 401 'Token has been revoked'.
    4. Calling POST /auth/logout again with the same token is rejected (401).
    5. Another token remains valid and unaffected.
    """
    app_cache.clear()
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        # Create two distinct tokens
        token_to_logout = create_access_token(user_id=10, role_level=2, username="admin_logout")
        token_active = create_access_token(user_id=11, role_level=2, username="admin_active")

        # Initially both tokens are valid
        payload1 = decode_token(token_to_logout)
        payload2 = decode_token(token_active)
        assert payload1["sub"] == "10"
        assert payload2["sub"] == "11"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test/api") as ac:
            # 1. Calling /auth/logout without token should fail
            res_no_auth = await ac.post("/auth/logout")
            assert res_no_auth.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]

            # 2. Call /auth/logout with valid Bearer token
            res_logout = await ac.post(
                "/auth/logout",
                headers={"Authorization": f"Bearer {token_to_logout}"}
            )
            assert res_logout.status_code == status.HTTP_200_OK
            data = res_logout.json()
            assert data["status"] == "ok"
            assert data["message"] == "Successfully logged out"

            # 3. Verify token is blacklisted
            assert is_token_blacklisted(token_to_logout, payload1) is True
            assert is_token_blacklisted(token_active, payload2) is False

            # 4. Attempting to decode the revoked token raises 401
            with pytest.raises(HTTPException) as exc_info:
                decode_token(token_to_logout)
            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert "revoked" in exc_info.value.detail.lower()

            # 5. Calling /auth/logout again with the revoked token fails (401)
            res_repeat = await ac.post(
                "/auth/logout",
                headers={"Authorization": f"Bearer {token_to_logout}"}
            )
            assert res_repeat.status_code == status.HTTP_401_UNAUTHORIZED
            assert "revoked" in res_repeat.json()["detail"].lower()

            # 6. Active token still works perfectly
            assert decode_token(token_active)["sub"] == "11"

    finally:
        app.dependency_overrides.pop(get_db, None)
        app_cache.clear()
