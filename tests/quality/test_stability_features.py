import pytest
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from httpx import AsyncClient, ASGITransport

from app.core.database import Base
from app.core.cache import TTLCache, app_cache
from app.models.user import User
from app.models.role import Role
from app.models.product import Product
from app.models.customer import Customer
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.recipe import Recipe
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


# ── 1. Health Check Tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_returns_ok(db_session: AsyncSession):
    """Test that /health endpoint returns status ok with database connected."""
    from app.main import app
    from app.core.database import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
    finally:
        app.dependency_overrides.clear()


# ── 2. Upload Size Limit Tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_size_limit_rejects_large_payload():
    """Test that requests with Content-Length > 5MB return HTTP 413."""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Send a POST with Content-Length header larger than 5MB
        # We don't need actual 5MB body, just the header claiming large size
        response = await client.post(
            "/products/",
            headers={"Content-Length": str(6 * 1024 * 1024)},
            content=b"fake",
        )
    assert response.status_code == 413
    data = response.json()
    assert "5 MB" in data["detail"]


@pytest.mark.asyncio
async def test_upload_size_limit_allows_small_payload():
    """Test that requests with Content-Length <= 5MB are NOT rejected by the middleware."""
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Small payload should pass the middleware (might fail auth/validation, but not 413)
        response = await client.post(
            "/products/",
            headers={"Content-Length": "100", "Content-Type": "application/json"},
            content=b'{"test": true}',
        )
    # Should NOT be 413 — may be 401/422 from actual route logic
    assert response.status_code != 413


# ── 3. Spending Cap Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spending_cap_rejects_order_over_50m(db_session: AsyncSession):
    """Test that creating an order exceeding Rp 50,000,000 raises HTTP 400."""
    from app.services.order_service import create_new_order, MAX_ORDER_AMOUNT
    from fastapi import HTTPException

    # Setup: customer
    customer = Customer(id=1, nama="Test Customer", nomor_wa="081200000001")
    db_session.add(customer)

    # Setup: stock item & recipe
    stock = StockItem(
        id=1,
        nama_item="Tepung",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("20.00"),
        stok_tersedia=Decimal("50000.00"),
        version=0,
    )
    db_session.add(stock)

    # Setup: expensive product — harga Rp 51,000,000
    product = Product(
        id=1,
        nama_produk="Super Expensive Cake",
        deskripsi="Very expensive",
        kategori="Premium",
        harga_jual=Decimal("51000000.00"),
        hpp_total=Decimal("10000000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
        slug="super-expensive-cake",
    )
    db_session.add(product)
    await db_session.commit()

    recipe = Recipe(product_id=1, stock_item_id=1, jumlah_dibutuhkan=Decimal("10.0000"))
    db_session.add(recipe)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await create_new_order(
            db=db_session,
            customer_id=1,
            items=[{"product_id": 1, "jumlah": 1}],
            metode_pengiriman="pickup",
        )

    assert exc_info.value.status_code == 400
    assert "melebihi batas maksimal" in exc_info.value.detail


@pytest.mark.asyncio
async def test_spending_cap_allows_order_under_50m(db_session: AsyncSession):
    """Test that creating an order under Rp 50,000,000 passes the spending cap check."""
    from app.services.order_service import create_new_order

    # Setup: customer
    customer = Customer(id=2, nama="Normal Customer", nomor_wa="081200000002")
    db_session.add(customer)

    stock = StockItem(
        id=2,
        nama_item="Gula",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("20.00"),
        stok_tersedia=Decimal("50000.00"),
        version=0,
    )
    db_session.add(stock)

    # Setup: normal product — harga Rp 250,000
    product = Product(
        id=2,
        nama_produk="Normal Cake",
        deskripsi="Normal cake",
        kategori="Kue Basah",
        harga_jual=Decimal("250000.00"),
        hpp_total=Decimal("100000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
        slug="normal-cake",
    )
    db_session.add(product)
    await db_session.commit()

    recipe = Recipe(product_id=2, stock_item_id=2, jumlah_dibutuhkan=Decimal("10.0000"))
    db_session.add(recipe)
    await db_session.commit()

    order = await create_new_order(
        db=db_session,
        customer_id=2,
        items=[{"product_id": 2, "jumlah": 1}],
        metode_pengiriman="pickup",
    )
    assert order.total_harga_pesanan == Decimal("250000.00")


# ── 4. Cache Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_basic_set_get():
    """Test basic TTLCache set and get."""
    cache = TTLCache(default_ttl=60)
    cache.set("test_key", {"data": "hello"})
    result = cache.get("test_key")
    assert result == {"data": "hello"}


@pytest.mark.asyncio
async def test_cache_expiry():
    """Test that cached entries expire after TTL."""
    cache = TTLCache(default_ttl=1)  # 1 second TTL
    cache.set("expire_key", "value", ttl=1)
    assert cache.get("expire_key") == "value"
    time.sleep(1.1)
    assert cache.get("expire_key") is None


@pytest.mark.asyncio
async def test_cache_invalidate_prefix():
    """Test that invalidate_prefix removes all matching keys."""
    cache = TTLCache(default_ttl=60)
    cache.set("products:list:True:None:False", [1, 2, 3])
    cache.set("products:list:False:None:True", [4, 5, 6])
    cache.set("faqs:list:0:100:False", ["faq1"])

    cache.invalidate_prefix("products:")

    assert cache.get("products:list:True:None:False") is None
    assert cache.get("products:list:False:None:True") is None
    # FAQ cache should still be intact
    assert cache.get("faqs:list:0:100:False") == ["faq1"]


@pytest.mark.asyncio
async def test_cache_invalidate_single_key():
    """Test that invalidate removes a single key."""
    cache = TTLCache(default_ttl=60)
    cache.set("key1", "value1")
    cache.set("key2", "value2")

    cache.invalidate("key1")

    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"
