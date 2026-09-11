import sys
import os
import asyncio
from unittest.mock import patch

# Ensure backend root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
import pytest

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.role import Role
from app.core.config import settings

async def run_tests():
    # Test DB setup
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, echo=False)
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    print("\nRunning Owner WhatsApp Numbers Endpoint Tests...")
    
    # 1. Setup Database & Roles/Users
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestSessionLocal() as db:
        # Create Roles
        role_owner = Role(id=1, nama_role="Owner", level=1)
        role_admin = Role(id=2, nama_role="Admin", level=2)
        db.add_all([role_owner, role_admin])
        
        # Create Users
        user_owner1 = User(username="owner1", password_hash="hash", role_id=1, is_active=True, phone_number="08123456789")
        user_owner2 = User(username="owner2", password_hash="hash", role_id=1, is_active=True, phone_number="+628987654321")
        user_owner_inactive = User(username="owner3", password_hash="hash", role_id=1, is_active=False, phone_number="08111111111")
        user_admin1 = User(username="admin1", password_hash="hash", role_id=2, is_active=True, phone_number="08222222222")
        # Same phone number to test dedup
        user_owner_dup = User(username="owner4", password_hash="hash", role_id=1, is_active=True, phone_number="08123456789")
        
        db.add_all([user_owner1, user_owner2, user_owner_inactive, user_admin1, user_owner_dup])
        await db.commit()

        # Direct service test to verify DB logic
        from app.services.user_service import get_owner_wa_numbers
        direct_numbers = await get_owner_wa_numbers(db)
        print(f"Direct service call returned: {direct_numbers}")
        assert "628123456789" in direct_numbers, "Direct service call failed"

    # 2. Test Endpoints with AsyncClient
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 2a. Missing Service Key (401)
        response = await client.get("/api/users/owner-numbers")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Missing Service Key blocked correctly (401)")

        # 2b. Invalid Service Key (401)
        response = await client.get(
            "/api/users/owner-numbers", 
            headers={"X-Service-Key": "invalid_key"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✓ Invalid Service Key blocked correctly (401)")

        # 2c. Valid Service Key
        response = await client.get(
            "/api/users/owner-numbers", 
            headers={"X-Service-Key": settings.service_api_key}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "numbers" in data
        numbers = data["numbers"]
        
        # Verify deduplication and normalization
        assert len(numbers) == 2, f"Expected 2 numbers, got {len(numbers)}: {numbers}"
        assert "628123456789" in numbers
        assert "628987654321" in numbers
        print(f"✓ Valid request succeeded, returned normalized numbers: {numbers}")

    app.dependency_overrides.clear()
    await test_engine.dispose()
    print("✅ All Owner WhatsApp Numbers Endpoint Tests Passed!")

@pytest.mark.asyncio
async def test_owner_numbers_endpoint():
    await run_tests()

if __name__ == "__main__":
    asyncio.run(run_tests())
