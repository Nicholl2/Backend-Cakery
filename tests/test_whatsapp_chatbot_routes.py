import sys
import os
import io
import time
import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import status
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

# Ensure backend root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.role import Role
from app.core.config import settings
from app.core.security import create_access_token
from app.core.cache import app_cache
from app.services.chatbot_notify import fetch_whatsapp_number
from app.services.buyer_auth_service import start_wa_verification


@pytest.fixture
async def test_session():
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, echo=False)
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as db:
        role_owner = Role(id=1, nama_role="Owner", level=1)
        role_admin = Role(id=2, nama_role="Admin", level=2)
        role_staff = Role(id=3, nama_role="Staff", level=3)
        db.add_all([role_owner, role_admin, role_staff])

        user_owner = User(id=1, username="owner", email="owner@test.com", password_hash="hash", role_id=1, is_active=True)
        user_admin = User(id=2, username="admin", email="admin@test.com", password_hash="hash", role_id=2, is_active=True)
        user_staff = User(id=3, username="staff", email="staff@test.com", password_hash="hash", role_id=3, is_active=True)
        db.add_all([user_owner, user_admin, user_staff])
        await db.commit()

    yield TestSessionLocal

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
def owner_token():
    return create_access_token(user_id=1, role_level=1, username="owner", role="owner")


@pytest.fixture
def admin_token():
    return create_access_token(user_id=2, role_level=2, username="admin", role="admin")


@pytest.fixture
def staff_token():
    return create_access_token(user_id=3, role_level=3, username="staff", role="staff")


def create_mock_client_ctx(mock_instance):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_ctx)


# ── 1. HELPER fetch_whatsapp_number TESTS ───────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_whatsapp_number_connected():
    mock_resp = httpx.Response(
        200,
        json={"keadaan": "tersambung", "nomor": "6281122334455"}
    )
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("app.services.chatbot_notify.httpx.AsyncClient", create_mock_client_ctx(mock_client)):
        num = await fetch_whatsapp_number()
        assert num == "6281122334455"


@pytest.mark.asyncio
async def test_fetch_whatsapp_number_disconnected_fallback():
    mock_resp = httpx.Response(
        200,
        json={"keadaan": "terputus", "nomor": None, "profile_name": None}
    )
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("app.services.chatbot_notify.httpx.AsyncClient", create_mock_client_ctx(mock_client)):
        num = await fetch_whatsapp_number()
        assert num == (settings.CHATBOT_WA_NUMBER or "6287881273160")


@pytest.mark.asyncio
async def test_fetch_whatsapp_number_waiting_scan_fallback():
    mock_resp = httpx.Response(
        200,
        json={"keadaan": "menunggu_scan", "nomor": None, "profile_name": None}
    )
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    with patch("app.services.chatbot_notify.httpx.AsyncClient", create_mock_client_ctx(mock_client)):
        num = await fetch_whatsapp_number()
        assert num == (settings.CHATBOT_WA_NUMBER or "6287881273160")


@pytest.mark.asyncio
async def test_fetch_whatsapp_number_error_fallback():
    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")

    with patch("app.services.chatbot_notify.httpx.AsyncClient", create_mock_client_ctx(mock_client)):
        num = await fetch_whatsapp_number()
        assert num == (settings.CHATBOT_WA_NUMBER or "6287881273160")


# ── 2. ADMIN WHATSAPP STATUS ENDPOINT ───────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_whatsapp_status_auth(test_session, owner_token, admin_token, staff_token):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Without Token -> 401
        res = await client.get("/admin/whatsapp/status")
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # 2. Staff Token (Role level 3) -> 403 Forbidden
        res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {staff_token}"})
        assert res.status_code == status.HTTP_403_FORBIDDEN

        # 3. Admin Token (Role level 2) -> 200 (Mock chatbot response with profile_name)
        mock_resp = httpx.Response(200, json={
            "keadaan": "tersambung",
            "nomor": "6281122334455",
            "profile_name": "Toti Cakery Official"
        })
        mock_ext_client = AsyncMock()
        mock_ext_client.get.return_value = mock_resp

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {admin_token}"})
            assert res.status_code == status.HTTP_200_OK
            assert res.json() == {
                "keadaan": "tersambung",
                "nomor": "6281122334455",
                "profile_name": "Toti Cakery Official"
            }

        # 4. Owner Token (Role level 1) -> 200
        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            assert res.json()["nomor"] == "6281122334455"
            assert res.json()["profile_name"] == "Toti Cakery Official"

        # 5. Chatbot Error -> 503
        mock_err_client = AsyncMock()
        mock_err_client.get.side_effect = httpx.ConnectTimeout("Timeout")
        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_err_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_admin_whatsapp_status_all_states(test_session, owner_token):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # State 1: tersambung
        mock_resp_connected = httpx.Response(200, json={
            "keadaan": "tersambung",
            "nomor": "6281122334455",
            "profile_name": "Toti Cakery Bot"
        })
        mock_ext_client = AsyncMock()
        mock_ext_client.get.return_value = mock_resp_connected

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["keadaan"] == "tersambung"
            assert data["nomor"] == "6281122334455"
            assert data["profile_name"] == "Toti Cakery Bot"

        # State 2: menunggu_scan
        mock_resp_waiting = httpx.Response(200, json={
            "keadaan": "menunggu_scan",
            "nomor": None,
            "profile_name": None
        })
        mock_ext_client.get.return_value = mock_resp_waiting

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["keadaan"] == "menunggu_scan"
            assert data["nomor"] is None
            assert data["profile_name"] is None

        # State 3: terputus
        mock_resp_disconnected = httpx.Response(200, json={
            "keadaan": "terputus",
            "nomor": None,
            "profile_name": None
        })
        mock_ext_client.get.return_value = mock_resp_disconnected

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/status", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            data = res.json()
            assert data["keadaan"] == "terputus"
            assert data["nomor"] is None
            assert data["profile_name"] is None


# ── 3. ADMIN WHATSAPP QR ENDPOINT ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_whatsapp_qr(test_session, owner_token, admin_token):
    transport = httpx.ASGITransport(app=app)
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Without Token -> 401
        res = await client.get("/admin/whatsapp/qr")
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # 2. Admin Token (level 2) -> 403 (Owner only)
        res = await client.get("/admin/whatsapp/qr", headers={"Authorization": f"Bearer {admin_token}"})
        assert res.status_code == status.HTTP_403_FORBIDDEN

        # 3. Owner Token with Chatbot 200 PNG -> 200 image/png + Cache-Control: no-store
        mock_resp = httpx.Response(200, content=fake_png, headers={"content-type": "image/png"})
        mock_ext_client = AsyncMock()
        mock_ext_client.get.return_value = mock_resp

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)):
            res = await client.get("/admin/whatsapp/qr", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            assert res.headers["content-type"] == "image/png"
            assert res.headers.get("cache-control") == "no-store"
            assert res.content == fake_png

        # 4. Chatbot 404 (already connected or no QR) -> 404
        mock_resp_404 = httpx.Response(404, json={"detail": "QR not found"})
        mock_404_client = AsyncMock()
        mock_404_client.get.return_value = mock_resp_404

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_404_client)):
            res = await client.get("/admin/whatsapp/qr", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_404_NOT_FOUND

        # 5. Chatbot Error -> 503
        mock_err_client = AsyncMock()
        mock_err_client.get.side_effect = httpx.ConnectError("Offline")

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_err_client)):
            res = await client.get("/admin/whatsapp/qr", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ── 4. ADMIN WHATSAPP GANTI NOMOR ENDPOINT ──────────────────────────────────

@pytest.mark.asyncio
async def test_admin_whatsapp_ganti_nomor(test_session, owner_token, admin_token):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Admin -> 403
        res = await client.post("/admin/whatsapp/ganti-nomor", headers={"Authorization": f"Bearer {admin_token}"})
        assert res.status_code == status.HTTP_403_FORBIDDEN

        # 2. Owner -> Success with audit log & timeout >= 65
        mock_status_resp = httpx.Response(200, json={"keadaan": "tersambung", "nomor": "628999888777"})
        mock_post_resp = httpx.Response(200, json={"status": "ok"})

        mock_ext_client = AsyncMock()
        mock_ext_client.get.return_value = mock_status_resp
        mock_ext_client.post.return_value = mock_post_resp

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_ext_client)), \
             patch("app.api.routes.admin_whatsapp.logger.info") as mock_logger_info:
            res = await client.post("/admin/whatsapp/ganti-nomor", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_200_OK
            assert res.json() == {"status": "ok"}
            # Verify audit log was recorded
            mock_logger_info.assert_called()
            log_str = " ".join([str(call.args) for call in mock_logger_info.call_args_list])
            assert "AUDIT" in log_str
            assert "628999888777" in log_str

        # 3. Chatbot timeout / error -> 503
        mock_timeout_client = AsyncMock()
        mock_timeout_client.get.return_value = mock_status_resp
        mock_timeout_client.post.side_effect = httpx.TimeoutException("Timeout")

        with patch("app.api.routes.admin_whatsapp.httpx.AsyncClient", create_mock_client_ctx(mock_timeout_client)):
            res = await client.post("/admin/whatsapp/ganti-nomor", headers={"Authorization": f"Bearer {owner_token}"})
            assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ── 5. INTEGRASI start_wa_verification ──────────────────────────────────────

@pytest.mark.asyncio
async def test_start_wa_verification_dynamic_number(test_session):
    TestSessionLocal = test_session
    async with TestSessionLocal() as db:
        # Mock fetch_whatsapp_number returning dynamic number
        with patch("app.services.buyer_auth_service.fetch_whatsapp_number", new_callable=AsyncMock, return_value="628555444333"):
            with patch.object(settings, "wa_verification_mode", "real"):
                res = await start_wa_verification(db, "081234567890")
                assert res["mock_mode"] is False
                assert res["deeplink"].startswith("https://wa.me/628555444333?text=VERIFIKASI%20")


# ── 6. PUBLIC KONTAK TOKO ENDPOINT & CACHING ────────────────────────────────

@pytest.mark.asyncio
async def test_public_kontak_toko_caching():
    app_cache.clear()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.api.routes.public.fetch_whatsapp_number", new_callable=AsyncMock, return_value="6281234567890") as mock_fetch:
            # First hit -> triggers fetch_whatsapp_number
            res1 = await client.get("/public/kontak-toko")
            assert res1.status_code == status.HTTP_200_OK
            assert res1.json() == {"whatsapp": "6281234567890"}
            assert mock_fetch.call_count == 1

            # Second hit -> served from in-memory cache, no new call to fetch_whatsapp_number
            res2 = await client.get("/public/kontak-toko")
            assert res2.status_code == status.HTTP_200_OK
            assert res2.json() == {"whatsapp": "6281234567890"}
            assert mock_fetch.call_count == 1  # Still 1 because cached!
