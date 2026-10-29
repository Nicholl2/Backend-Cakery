from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.faq_item import FaqItem
from app.models.product import Category, Product
from app.models.role import Role
from app.models.user import User


@pytest.fixture
async def public_search_client():
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
    try:
        async with session_factory() as db:
            role = Role(nama_role="Admin", level=2)
            db.add(role)
            await db.flush()
            user = User(
                username="search-admin",
                email="search-admin@example.test",
                password_hash="not-used",
                role_id=role.id,
            )
            category = Category(name="Kue Bolu", description="Kategori bolu")
            db.add_all([user, category])
            await db.flush()
            db.add_all(
                [
                    Product(
                        nama_produk="Bolu Cokelat",
                        deskripsi="Bolu lembut rasa cokelat",
                        kategori="Bolu",
                        category_id=category.id,
                        harga_jual=Decimal("45000"),
                        is_active=True,
                        image_url="https://example.test/bolu.jpg",
                    ),
                    Product(
                        nama_produk="Bolu Internal",
                        is_active=False,
                        harga_jual=Decimal("1"),
                    ),
                    FaqItem(
                        pertanyaan="Bagaimana pembayaran bolu?",
                        jawaban="Pembayaran tersedia melalui QRIS.",
                        created_by=user.id,
                        is_active=True,
                    ),
                    FaqItem(
                        pertanyaan="FAQ draft bolu",
                        jawaban="Informasi internal yang belum dipublikasikan.",
                        created_by=user.id,
                        is_active=False,
                    ),
                ]
            )
            await db.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_search_returns_only_active_public_content(public_search_client):
    response = await public_search_client.get("/public/search", params={"q": "bolu"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "bolu"
    assert [item["nama_produk"] for item in body["products"]] == ["Bolu Cokelat"]
    assert [item["pertanyaan"] for item in body["faqs"]] == ["Bagaimana pembayaran bolu?"]
    assert body["store_info"] == []
    assert body["total"] == 2
    assert "hpp_total" not in body["products"][0]
    assert "created_by" not in body["faqs"][0]


@pytest.mark.asyncio
async def test_public_search_returns_store_contact_action_without_external_lookup(public_search_client):
    response = await public_search_client.get("/public/search", params={"q": "kontak toko"})

    assert response.status_code == 200
    assert response.json()["store_info"] == [
        {
            "key": "contact",
            "title": "Kontak Toti Cakery",
            "description": "Lihat nomor WhatsApp resmi toko untuk menghubungi Toti Cakery.",
            "action": "fetch_contact_endpoint",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["", "a", "<script>alert(1)</script>", "bolu\u0000cokelat", "bolu\ncokelat", "x" * 81],
)
async def test_public_search_rejects_short_script_and_oversized_queries(
    public_search_client,
    query,
):
    response = await public_search_client.get("/public/search", params={"q": query})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_public_search_treats_sql_wildcards_as_literal_text(public_search_client):
    response = await public_search_client.get("/public/search", params={"q": "% bolu"})

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert response.json()["faqs"] == []
