import pytest
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.store_customization import StoreCustomization
from app.models.user import User
from app.services import store_customization_service


@pytest.fixture
async def store_customization_client(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db

    async with session_factory() as db:
        owner_role = Role(nama_role="Owner", level=1)
        admin_role = Role(nama_role="Admin", level=2)
        db.add_all([owner_role, admin_role])
        await db.flush()
        owner = User(
            username="customization-owner",
            email="customization-owner@example.test",
            password_hash="not-used",
            role_id=owner_role.id,
            is_active=True,
        )
        admin = User(
            username="customization-admin",
            email="customization-admin@example.test",
            password_hash="not-used",
            role_id=admin_role.id,
            is_active=True,
        )
        db.add_all([owner, admin])
        await db.flush()
        db.add(
            StoreCustomization(
                id=1,
                hero_title_id="Judul Indonesia",
                hero_title_en="English title",
                hero_subtitle_id="Subjudul Indonesia",
                hero_subtitle_en="English subtitle",
                hero_image_url="https://res.cloudinary.com/mrje22up/image/upload/test-banner.jpg",
                about_title_id="Tentang Toti",
                about_title_en="About Toti",
                about_description_id="Deskripsi Indonesia",
                about_description_en="English description",
                store_address=None,
                maps_embed_url="https://www.google.com/maps/embed?pb=test",
            )
        )
        await db.commit()
        owner_token = create_access_token(owner.id, 1, owner.username)
        admin_token = create_access_token(admin.id, 2, admin.username)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield {
                "client": client,
                "owner_headers": {"Authorization": f"Bearer {owner_token}"},
                "admin_headers": {"Authorization": f"Bearer {admin_token}"},
                "session_factory": session_factory,
                "monkeypatch": monkeypatch,
            }
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_store_customization_returns_only_public_bilingual_content(store_customization_client):
    response = await store_customization_client["client"].get("/public/store-customization")

    assert response.status_code == 200
    body = response.json()
    assert body["content"]["id"]["hero_title"] == "Judul Indonesia"
    assert body["content"]["en"]["hero_title"] == "English title"
    assert body["store_address"] is None
    assert "updated_by" not in body


@pytest.mark.asyncio
async def test_store_customization_patch_requires_owner(store_customization_client):
    client = store_customization_client["client"]
    payload = {"content": {"en": {"hero_title": "Updated title"}}}

    anonymous = await client.patch("/settings/store-customization", json=payload)
    admin = await client.patch(
        "/settings/store-customization",
        json=payload,
        headers=store_customization_client["admin_headers"],
    )

    assert anonymous.status_code in {401, 403}
    assert admin.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_partially_update_without_overwriting_other_locale(store_customization_client):
    response = await store_customization_client["client"].patch(
        "/settings/store-customization",
        json={"content": {"en": {"hero_title": "New English title"}}},
        headers=store_customization_client["owner_headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["content"]["en"]["hero_title"] == "New English title"
    assert body["content"]["en"]["hero_subtitle"] == "English subtitle"
    assert body["content"]["id"]["hero_title"] == "Judul Indonesia"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"content": {"id": {"hero_title": "<script>alert(1)</script>"}}},
        {"hero_image_url": "javascript:alert(1)"},
        {"hero_image_url": "https://example.test/banner.png"},
        {"maps_embed_url": "https://example.test/maps/embed?pb=x"},
        {"unknown_field": "mass assignment"},
        {"content": {"en": {}}},
        {},
    ],
)
async def test_owner_patch_rejects_unsafe_or_empty_input(store_customization_client, payload):
    response = await store_customization_client["client"].patch(
        "/settings/store-customization",
        json=payload,
        headers=store_customization_client["owner_headers"],
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_banner_upload_rejects_mime_spoofed_file(store_customization_client):
    response = await store_customization_client["client"].post(
        "/settings/store-customization/hero-image",
        files={"file": ("banner.jpg", b"<script>alert(1)</script>", "image/jpeg")},
        headers=store_customization_client["owner_headers"],
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_banner_upload_requires_owner(store_customization_client):
    response = await store_customization_client["client"].post(
        "/settings/store-customization/hero-image",
        files={"file": ("banner.jpg", b"\xff\xd8\xffjpeg-data", "image/jpeg")},
    )

    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_banner_upload_rejects_files_over_5_mib(store_customization_client):
    response = await store_customization_client["client"].post(
        "/settings/store-customization/hero-image",
        files={"file": ("banner.jpg", b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024), "image/jpeg")},
        headers=store_customization_client["owner_headers"],
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_owner_banner_upload_returns_secure_cloudinary_url(store_customization_client):
    async def fake_upload(file, folder, max_size):
        assert folder == "toti-cakery/store"
        assert max_size == 5 * 1024 * 1024
        assert file.filename.startswith("hero-")
        assert file.filename.endswith(".jpg")
        assert await file.read(3) == b"\xff\xd8\xff"
        return "https://res.cloudinary.com/mrje22up/image/upload/new-banner.jpg"

    store_customization_client["monkeypatch"].setattr(
        store_customization_service,
        "upload_image_to_cloudinary",
        fake_upload,
    )
    response = await store_customization_client["client"].post(
        "/settings/store-customization/hero-image",
        files={"file": ("banner.jpg", b"\xff\xd8\xffjpeg-data", "image/jpeg")},
        headers=store_customization_client["owner_headers"],
    )

    assert response.status_code == 200
    assert response.json() == {
        "hero_image_url": "https://res.cloudinary.com/mrje22up/image/upload/new-banner.jpg"
    }
