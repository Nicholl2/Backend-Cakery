import sys
import os
import asyncio
import pytest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

# Ensure backend root directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.role import Role
from app.core.security import create_access_token, hash_password
from app.schemas.user import UserCreate, UserOut
from app.services import user_service


@pytest.mark.asyncio
async def test_user_creation_and_eager_loading():
    """Test user creation and ensure eager loading prevents MissingGreenlet error."""
    # Test DB setup
    test_db_url = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(test_db_url, poolclass=StaticPool, echo=False)
    test_session_local = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with test_session_local() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 1. Initialize Tables & Roles
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_local() as db:
        role_owner = Role(id=1, nama_role="Owner", level=1)
        role_admin = Role(id=2, nama_role="Admin", level=2)
        role_staff = Role(id=3, nama_role="Staff", level=3)
        db.add_all([role_owner, role_admin, role_staff])

        owner_user = User(
            id=1,
            username="testowner",
            password_hash=hash_password("Password_123"),
            role_id=1,
            is_active=True,
            phone_number="081234567890",
            nomor_wa_admin="6281234567890",
        )
        db.add(owner_user)
        await db.commit()

    owner_token = create_access_token(user_id=1, role_level=1, username="testowner", role="owner")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    # 2. Test via HTTP Client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # (a) POST /users - Create Admin user with role_id=2
        admin_payload = {
            "username": "newadmin",
            "password": "Password_123",
            "role_id": 2,
            "email": "newadmin@example.com",
            "phone_number": "081211112222",
            "nomor_wa_admin": "6281211112222"
        }
        res_admin = await client.post("/api/users", json=admin_payload, headers=owner_headers)
        assert res_admin.status_code == 201, f"Expected 201, got {res_admin.status_code}: {res_admin.text}"
        data_admin = res_admin.json()
        assert data_admin["username"] == "newadmin"
        assert data_admin["role_id"] == 2
        assert data_admin["role_name"] == "Admin"
        assert data_admin["role"] == "Admin"
        new_admin_id = data_admin["id"]

        # (b) POST /users - Create Staff user with role="staff" (string role mapping)
        staff_payload = {
            "username": "newstaff",
            "password": "Password_123",
            "role": "staff",
            "email": "newstaff@example.com",
            "phone_number": "081233334444"
        }
        res_staff = await client.post("/api/users", json=staff_payload, headers=owner_headers)
        assert res_staff.status_code == 201, f"Expected 201, got {res_staff.status_code}: {res_staff.text}"
        data_staff = res_staff.json()
        assert data_staff["username"] == "newstaff"
        assert data_staff["role_id"] == 3
        assert data_staff["role_name"] == "Staff"
        assert data_staff["role"] == "Staff"
        new_staff_id = data_staff["id"]

        # (c) GET /users - List all users with eager loaded roles
        res_list = await client.get("/api/users", headers=owner_headers)
        assert res_list.status_code == 200
        users_list = res_list.json()
        assert len(users_list) == 3
        roles_in_list = {u["username"]: u["role_name"] for u in users_list}
        assert roles_in_list["testowner"] == "Owner"
        assert roles_in_list["newadmin"] == "Admin"
        assert roles_in_list["newstaff"] == "Staff"

        # (d) GET /users/me - Profiling endpoints with role
        admin_token = create_access_token(user_id=new_admin_id, role_level=2, username="newadmin", role="admin")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        res_me = await client.get("/api/users/me", headers=admin_headers)
        assert res_me.status_code == 200
        me_data = res_me.json()
        assert me_data["username"] == "newadmin"
        assert me_data["role_name"] == "Admin"

        # (e) PUT /users/me - Update own profile
        res_put_me = await client.put(
            "/api/users/me",
            json={"email": "updatedadmin@example.com"},
            headers=admin_headers
        )
        assert res_put_me.status_code == 200
        put_me_data = res_put_me.json()
        assert put_me_data["email"] == "updatedadmin@example.com"
        assert put_me_data["role_name"] == "Admin"

        # (f) PUT /users/{id} - Owner edits internal user details (switch staff to admin)
        res_edit = await client.put(
            f"/api/users/{new_staff_id}",
            json={"role": "admin", "handles_takeover": True},
            headers=owner_headers
        )
        assert res_edit.status_code == 200
        edit_data = res_edit.json()
        assert edit_data["role_id"] == 2
        assert edit_data["role_name"] == "Admin"
        assert edit_data["handles_takeover"] is True

        # (g) PATCH /users/{id}/deactivate - Deactivate user
        res_deact = await client.patch(
            f"/api/users/{new_staff_id}/deactivate",
            headers=owner_headers
        )
        assert res_deact.status_code == 200
        deact_data = res_deact.json()
        assert deact_data["is_active"] is False
        assert deact_data["role_name"] == "Admin"

        # (h) Non-owner forbidden check (Admin cannot create user)
        res_forbidden = await client.post("/api/users", json=staff_payload, headers=admin_headers)
        assert res_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_direct_service_create_user_in_isolated_sessions():
    """Verify create_user in fresh session produces Pydantic UserOut without MissingGreenlet."""
    test_db_url = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(test_db_url, poolclass=StaticPool, echo=False)
    test_session_local = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Session 1: seed roles
    async with test_session_local() as db:
        db.add_all([
            Role(id=1, nama_role="Owner", level=1),
            Role(id=2, nama_role="Admin", level=2),
            Role(id=3, nama_role="Staff", level=3)
        ])
        await db.commit()

    # Session 2: create user in completely fresh session
    async with test_session_local() as db:
        user_in = UserCreate(
            username="isolated_staff",
            password="Password_123",
            role_id=3,
            phone_number="081999998888"
        )
        created_user = await user_service.create_user(db, user_in)
        # Serialize to UserOut (this triggers property role_name)
        out = UserOut.model_validate(created_user)
        assert out.role_name == "Staff"
        assert out.role == "Staff"
        assert out.username == "isolated_staff"
        assert out.role_id == 3
