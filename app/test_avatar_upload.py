import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import io
from unittest.mock import patch, MagicMock
from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import httpx

from app.core.database import Base, get_db
from app.core.config import settings
from app.models.buyer import Buyer
from app.models.user import User
from app.models.role import Role
from app.utils.cloudinary_helper import (
    upload_image_to_cloudinary,
    ALLOWED_IMAGE_MIME_TYPES,
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_AVATAR_FILE_SIZE,
)
from app.repositories import buyer_repo, user_repo
from app.services import buyer_auth_service, user_service
from app.core.security import create_access_token, hash_password
from app.main import app

# Create in-memory SQLite engine for tests
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


async def run_tests():
    print("🧪 Running User & Buyer Avatar Upload Unit & Integration Tests...\n")

    # 1. Initialize Test Database Tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


    # 2. Test File Validation Unit Tests
    print("1. Testing File Validation Logic in upload_image_to_cloudinary...")

    # (a) Invalid MIME type
    bad_file = UploadFile(
        file=io.BytesIO(b"not an image content"),
        filename="test.pdf",
        headers={"content-type": "application/pdf"}
    )
    try:
        await upload_image_to_cloudinary(bad_file)
        assert False, "Should fail on invalid MIME type"
    except HTTPException as e:
        assert e.status_code == 400
        print("  ✓ Invalid MIME type / extension correctly rejected with HTTP 400")

    # (b) Exceeding max file size (5MB + 1 byte)
    oversized_buffer = io.BytesIO(b"x" * (MAX_AVATAR_FILE_SIZE + 10))
    oversized_file = UploadFile(
        file=oversized_buffer,
        filename="huge.png",
        headers={"content-type": "image/png"}
    )
    try:
        await upload_image_to_cloudinary(oversized_file)
        assert False, "Should fail on oversized file"
    except HTTPException as e:
        assert e.status_code == 400
        print("  ✓ Oversized file (>5MB) correctly rejected with HTTP 400")

    # (c) Valid file with mocked Cloudinary upload
    valid_buffer = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    valid_file = UploadFile(
        file=valid_buffer,
        filename="avatar.png",
        headers={"content-type": "image/png"}
    )

    mock_secure_url = "https://res.cloudinary.com/demo/image/upload/v12345/toti-cakery/avatars/sample.png"

    with patch("cloudinary.uploader.upload", return_value={"secure_url": mock_secure_url}) as mock_upload:
        with patch.object(settings, "cloudinary_cloud_name", "dummy_cloud"), \
             patch.object(settings, "cloudinary_api_key", "dummy_key"), \
             patch.object(settings, "cloudinary_api_secret", "dummy_secret"):
            url = await upload_image_to_cloudinary(valid_file, folder="toti-cakery/avatars")
            assert url == mock_secure_url
            mock_upload.assert_called_once()
            print("  ✓ Valid image uploaded directly to Cloudinary folder toti-cakery/avatars/")

    # 3. Seed test buyer and user in test database
    async with TestSessionLocal() as db:
        # Seed role
        role_owner = Role(id=1, nama_role="Owner", level=1)
        role_admin = Role(id=2, nama_role="Admin", level=2)
        db.add_all([role_owner, role_admin])

        test_buyer = Buyer(
            name="Jane Doe",
            email="jane@example.com",
            phone="081234567890",
            password_hash=hash_password("password123"),
            is_verified=True,
            is_active=True,
        )
        db.add(test_buyer)

        test_user = User(
            username="admin_jane",
            email="admin_jane@example.com",
            phone_number="081298765432",
            password_hash=hash_password("adminpass123"),
            role_id=2,
            is_active=True,
        )
        db.add(test_user)
        await db.commit()
        await db.refresh(test_buyer)
        await db.refresh(test_user)
        buyer_id = test_buyer.id
        user_id = test_user.id

    buyer_token = create_access_token(user_id=buyer_id, role_level=0, username="jane@example.com", role="buyer")
    user_token = create_access_token(user_id=user_id, role_level=2, username="admin_jane")

    # 4. Test API Endpoints using AsyncClient
    print("\n2. Testing Buyer Avatar API Endpoints...")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # (a) Test GET /buyers/me before avatar upload
        me_res = await client.get("/buyers/me", headers={"Authorization": f"Bearer {buyer_token}"})
        assert me_res.status_code == 200, f"Failed: {me_res.text}"
        assert me_res.json()["avatar_url"] is None
        print("  ✓ GET /buyers/me returns profile with avatar_url=None")

        # (b) Test POST /buyers/me/avatar
        img_data = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        with patch("cloudinary.uploader.upload", return_value={"secure_url": mock_secure_url}), \
             patch.object(settings, "cloudinary_cloud_name", "dummy_cloud"), \
             patch.object(settings, "cloudinary_api_key", "dummy_key"), \
             patch.object(settings, "cloudinary_api_secret", "dummy_secret"):
            upload_res = await client.post(
                "/buyers/me/avatar",
                headers={"Authorization": f"Bearer {buyer_token}"},
                files={"file": ("profile.jpg", img_data, "image/jpeg")}
            )
            assert upload_res.status_code == 200, f"Upload failed: {upload_res.text}"
            res_json = upload_res.json()
            assert res_json["avatar_url"] == mock_secure_url
            assert res_json["email"] == "jane@example.com"
            print(f"  ✓ POST /buyers/me/avatar succeeded and returned updated profile: {res_json['avatar_url']}")

        # (c) Test GET /v1/buyers/me (alias route)
        alias_res = await client.get("/v1/buyers/me", headers={"Authorization": f"Bearer {buyer_token}"})
        assert alias_res.status_code == 200, f"Alias failed: {alias_res.status_code} - {alias_res.text}"
        assert alias_res.json()["avatar_url"] == mock_secure_url
        print("  ✓ GET /v1/buyers/me alias returns updated avatar_url")

        # 5. Test User Avatar API Endpoint
        print("\n3. Testing Internal User Avatar API Endpoint...")
        user_img_data = io.BytesIO(b"RIFF" + b"\x00" * 50)
        with patch("cloudinary.uploader.upload", return_value={"secure_url": mock_secure_url}), \
             patch.object(settings, "cloudinary_cloud_name", "dummy_cloud"), \
             patch.object(settings, "cloudinary_api_key", "dummy_key"), \
             patch.object(settings, "cloudinary_api_secret", "dummy_secret"):
            user_upload_res = await client.post(
                "/users/me/avatar",
                headers={"Authorization": f"Bearer {user_token}"},
                files={"file": ("user_avatar.webp", user_img_data, "image/webp")}
            )
            assert user_upload_res.status_code == 200, f"User upload failed: {user_upload_res.text}"
            user_res_json = user_upload_res.json()
            assert user_res_json["avatar_url"] == mock_secure_url
            assert user_res_json["username"] == "admin_jane"
            print(f"  ✓ POST /users/me/avatar succeeded and returned user profile with avatar_url: {user_res_json['avatar_url']}")

    print("\n🎉 All User & Buyer Avatar Upload Tests Passed Successfully!")


if __name__ == "__main__":
    asyncio.run(run_tests())
