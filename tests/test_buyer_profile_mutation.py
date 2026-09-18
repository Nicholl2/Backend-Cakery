"""
Test suite for Buyer Profile Mutation endpoints:
  - POST /buyers/me/change-password
  - PATCH /buyers/me/phone

Coverage:
  1. Change password — valid current_password                   → 200
  2. Change password — wrong current_password                   → 400
  3. Change password — new_password < 6 chars                   → 422
  4. Change phone — valid password, unique phone                → 200
  5. Change phone — wrong current_password                      → 400
  6. Change phone — duplicate phone (used by other buyer)       → 400
  7. Unauthenticated access (no Bearer token)                   → 401
  8. Non-buyer role token                                       → 401
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import settings
from app.core.migrations import (
    ensure_buyer_columns,
    ensure_otp_columns,
)
from app.main import app
from app.models.buyer import Buyer
from app.repositories import buyer_repo
from app.core.security import create_access_token, hash_password


passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {label}")
    else:
        failed += 1
        print(f"  ❌ {label} — {detail}")


async def run_tests():
    global passed, failed
    print("🚀 Starting Buyer Profile Mutation Tests...\n")

    # ── Setup in-memory SQLite ────────────────────────────────────────────────
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    settings.service_api_key = getattr(settings, "service_api_key", "test_key") or "test_key"

    # ── Create tables & migrations ────────────────────────────────────────────
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_buyer_columns(conn)
        await ensure_otp_columns(conn)

    # ── Seed two test buyers ──────────────────────────────────────────────────
    BUYER1_PASSWORD = "password123"
    BUYER2_PASSWORD = "password456"

    async with TestSessionLocal() as db:
        buyer1 = await buyer_repo.create_buyer(
            db,
            name="Buyer One",
            email="buyer1@test.com",
            phone="6281911111111",
            password_hash=hash_password(BUYER1_PASSWORD),
            is_verified=True,
        )
        buyer2 = await buyer_repo.create_buyer(
            db,
            name="Buyer Two",
            email="buyer2@test.com",
            phone="6281922222222",
            password_hash=hash_password(BUYER2_PASSWORD),
            is_verified=True,
        )

    buyer1_token = create_access_token(
        user_id=buyer1.id, role_level=0, username=buyer1.email, role="buyer"
    )
    buyer2_token = create_access_token(
        user_id=buyer2.id, role_level=0, username=buyer2.email, role="buyer"
    )

    # Non-buyer (seller/admin) token
    seller_token = create_access_token(
        user_id=9999, role_level=2, username="admin_user", role="admin"
    )

    headers_b1 = {"Authorization": f"Bearer {buyer1_token}"}
    headers_b2 = {"Authorization": f"Bearer {buyer2_token}"}
    headers_seller = {"Authorization": f"Bearer {seller_token}"}

    # ═══════════════════════════════════════════════════════════════════════════
    # TEST SUITE
    # ═══════════════════════════════════════════════════════════════════════════

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api") as client:

        # ── 1. Change password — valid current_password ──────────────────────
        print("── Test 1: Change Password (valid) ──")
        r = await client.post(
            "/buyers/me/change-password",
            json={"current_password": BUYER1_PASSWORD, "new_password": "newpass123"},
            headers=headers_b1,
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}: {r.text}")
        check("Message correct", r.json().get("message") == "Password berhasil diperbarui.", r.text)

        # Verify new password works for subsequent requests
        NEW_BUYER1_PASSWORD = "newpass123"

        # ── 2. Change password — wrong current_password ──────────────────────
        print("\n── Test 2: Change Password (wrong current password) ──")
        r = await client.post(
            "/buyers/me/change-password",
            json={"current_password": "wrongpassword", "new_password": "newpass456"},
            headers=headers_b1,
        )
        check("Status 400", r.status_code == 400, f"got {r.status_code}: {r.text}")
        check(
            "Error detail",
            "Password saat ini tidak sesuai" in r.json().get("detail", ""),
            r.text,
        )

        # ── 3. Change password — new_password too short ──────────────────────
        print("\n── Test 3: Change Password (new_password < 6 chars) ──")
        r = await client.post(
            "/buyers/me/change-password",
            json={"current_password": NEW_BUYER1_PASSWORD, "new_password": "abc"},
            headers=headers_b1,
        )
        check("Status 422", r.status_code == 422, f"got {r.status_code}: {r.text}")

        # ── 4. Change phone — valid password, unique phone ───────────────────
        print("\n── Test 4: Change Phone (valid) ──")
        r = await client.patch(
            "/buyers/me/phone",
            json={"phone": "081933333333", "current_password": NEW_BUYER1_PASSWORD},
            headers=headers_b1,
        )
        check("Status 200", r.status_code == 200, f"got {r.status_code}: {r.text}")
        body = r.json()
        # normalize_phone converts 0819... → 6281933333333
        check(
            "Phone updated",
            body.get("phone") == "6281933333333",
            f"got phone={body.get('phone')}",
        )
        check("Returns profile fields", "email" in body and "name" in body, r.text)

        # ── 5. Change phone — wrong current_password ─────────────────────────
        print("\n── Test 5: Change Phone (wrong current password) ──")
        r = await client.patch(
            "/buyers/me/phone",
            json={"phone": "081944444444", "current_password": "wrongpwd"},
            headers=headers_b1,
        )
        check("Status 400", r.status_code == 400, f"got {r.status_code}: {r.text}")
        check(
            "Error detail",
            "Password saat ini tidak sesuai" in r.json().get("detail", ""),
            r.text,
        )

        # ── 6. Change phone — duplicate phone ────────────────────────────────
        print("\n── Test 6: Change Phone (duplicate — used by buyer2) ──")
        r = await client.patch(
            "/buyers/me/phone",
            json={"phone": "6281922222222", "current_password": NEW_BUYER1_PASSWORD},
            headers=headers_b1,
        )
        check("Status 400", r.status_code == 400, f"got {r.status_code}: {r.text}")
        check(
            "Error detail",
            "Nomor WhatsApp sudah terdaftar" in r.json().get("detail", ""),
            r.text,
        )

        # ── 7. Unauthenticated access (no Bearer token) ─────────────────────
        print("\n── Test 7: Unauthenticated access ──")
        r1 = await client.post(
            "/buyers/me/change-password",
            json={"current_password": "x", "new_password": "yyyyyy"},
        )
        r2 = await client.patch(
            "/buyers/me/phone",
            json={"phone": "081900000000", "current_password": "x"},
        )
        check(
            "Change password → 401",
            r1.status_code == 401,
            f"got {r1.status_code}: {r1.text}",
        )
        check(
            "Change phone → 401",
            r2.status_code == 401,
            f"got {r2.status_code}: {r2.text}",
        )

        # ── 8. Non-buyer role token ──────────────────────────────────────────
        print("\n── Test 8: Non-buyer role token ──")
        r1 = await client.post(
            "/buyers/me/change-password",
            json={"current_password": "x", "new_password": "yyyyyy"},
            headers=headers_seller,
        )
        r2 = await client.patch(
            "/buyers/me/phone",
            json={"phone": "081900000000", "current_password": "x"},
            headers=headers_seller,
        )
        check(
            "Change password → 401 (not a buyer)",
            r1.status_code == 401,
            f"got {r1.status_code}: {r1.text}",
        )
        check(
            "Change phone → 401 (not a buyer)",
            r2.status_code == 401,
            f"got {r2.status_code}: {r2.text}",
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed} checks")
    print(f"{'═' * 60}")

    app.dependency_overrides.clear()
    await test_engine.dispose()

    if failed > 0:
        raise AssertionError(f"{failed} test(s) failed")


def test_buyer_profile_mutation():
    """pytest entry point — runs the full async test suite."""
    asyncio.run(run_tests())


if __name__ == "__main__":
    asyncio.run(run_tests())
