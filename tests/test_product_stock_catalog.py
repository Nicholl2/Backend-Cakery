import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
import pytest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.product import Product
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum
from app.models.recipe import Recipe
from app.schemas.product import ProductOut, ProductResponse, ProductRead, ProductCreate, ProductUpdate
from app.services import product_service, order_service
from fastapi import HTTPException


@pytest.fixture(name="test_session")
async def test_session_fixture():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_pydantic_schema_aliases_and_computed_in_stock():
    """Test 1: Memverifikasi Pydantic schema ProductResponse / ProductRead dan computed property is_in_stock."""
    assert ProductResponse is ProductOut
    assert ProductRead is ProductOut

    # Case A: is_available=True, stock > 0 -> is_in_stock=True
    p1 = ProductResponse(
        id=1,
        nama_produk="Kue Enak",
        hpp_total=Decimal("10000.00"),
        harga_jual=Decimal("20000.00"),
        is_active=True,
        is_available=True,
        stock_quantity=5,
    )
    assert p1.is_available is True
    assert p1.stock_quantity == 5
    assert p1.is_in_stock is True

    # Case B: is_available=True, stock = 0 -> is_in_stock=False
    p2 = ProductResponse(
        id=2,
        nama_produk="Kue Habis",
        hpp_total=Decimal("10000.00"),
        harga_jual=Decimal("20000.00"),
        is_active=True,
        is_available=True,
        stock_quantity=0,
    )
    assert p2.is_available is True
    assert p2.stock_quantity == 0
    assert p2.is_in_stock is False

    # Case C: is_available=False (seller manual off), stock > 0 -> is_in_stock=False
    p3 = ProductResponse(
        id=3,
        nama_produk="Kue Nonaktif Seller",
        hpp_total=Decimal("10000.00"),
        harga_jual=Decimal("20000.00"),
        is_active=True,
        is_available=False,
        stock_quantity=10,
    )
    assert p3.is_available is False
    assert p3.stock_quantity == 10
    assert p3.is_in_stock is False


@pytest.mark.asyncio
async def test_product_stock_calculation_and_queries(test_session: AsyncSession):
    """Test 2: Verifikasi kalkulasi stok dari resep dan perilaku query katalog produk."""
    db = test_session

    # Setup Stock Items
    tepung = StockItem(
        id=1,
        nama_item="Tepung Terigu",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("20.00"),
        stok_tersedia=Decimal("1000.00"), # 1000 gram
        version=0,
    )
    telur = StockItem(
        id=2,
        nama_item="Telur Ayam",
        satuan=SatuanEnum.pcs,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("2000.00"),
        stok_tersedia=Decimal("10.00"), # 10 pcs
        version=0,
    )
    db.add_all([tepung, telur])
    await db.commit()

    # Product A: Ready stock (needs 200g tepung + 2 pcs telur -> max 5 from tepung, 5 from telur -> stock = 5)
    prod_ready = Product(
        id=1,
        nama_produk="Kue Bolu Ready",
        deskripsi="Bolu lembut",
        kategori="Bolu",
        harga_jual=Decimal("50000.00"),
        hpp_total=Decimal("20000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
    )
    # Product B: Out of stock (needs 3000g tepung -> but only 1000g available -> stock = 0)
    prod_depleted = Product(
        id=2,
        nama_produk="Kue Lapis Habis",
        deskripsi="Lapis legit",
        kategori="Lapis",
        harga_jual=Decimal("150000.00"),
        hpp_total=Decimal("80000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
    )
    # Product C: Seller manually unavailable (is_available=False), has stock
    prod_manual_off = Product(
        id=3,
        nama_produk="Kue Musiman Tutup",
        deskripsi="Kue tutup",
        kategori="Musiman",
        harga_jual=Decimal("75000.00"),
        hpp_total=Decimal("30000.00"),
        is_active=True,
        is_available=False,
        minimum_order=1,
    )
    db.add_all([prod_ready, prod_depleted, prod_manual_off])
    await db.commit()

    # Add Recipes
    r1 = Recipe(product_id=1, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("200.0000"))
    r2 = Recipe(product_id=1, stock_item_id=telur.id, jumlah_dibutuhkan=Decimal("2.0000"))
    r3 = Recipe(product_id=2, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("3000.0000"))
    r4 = Recipe(product_id=3, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("100.0000"))
    db.add_all([r1, r2, r3, r4])
    await db.commit()

    # 1. Check Model Properties
    p1 = await product_service.get_product_or_404(db, 1)
    assert p1.stock_quantity == 5
    assert p1.is_in_stock is True

    p2 = await product_service.get_product_or_404(db, 2)
    assert p2.stock_quantity == 0
    assert p2.is_in_stock is False

    p3 = await product_service.get_product_or_404(db, 3)
    assert p3.stock_quantity == 10
    assert p3.is_available is False
    assert p3.is_in_stock is False

    # 2. Test get_all_products default (only_available=False) -> MUST return ALL products including depleted
    all_prods = await product_service.get_all_products(db, only_active=True, only_available=False)
    assert len(all_prods) == 3
    names = [p.nama_produk for p in all_prods]
    assert "Kue Bolu Ready" in names
    assert "Kue Lapis Habis" in names
    assert "Kue Musiman Tutup" in names

    # Verify depleted product has stock_quantity == 0 and is_in_stock == False
    depleted_out = next(p for p in all_prods if p.nama_produk == "Kue Lapis Habis")
    assert depleted_out.stock_quantity == 0
    assert depleted_out.is_in_stock is False

    # 3. Test get_all_products with only_available=True -> MUST filter only is_in_stock == True
    available_prods = await product_service.get_all_products(db, only_active=True, only_available=True)
    assert len(available_prods) == 1
    assert available_prods[0].nama_produk == "Kue Bolu Ready"
    assert available_prods[0].is_in_stock is True


@pytest.mark.asyncio
async def test_order_stock_validation_protection(test_session: AsyncSession):
    """Test 3: Memverifikasi proteksi validasi order (POST /orders & create_new_order)."""
    db = test_session

    # Setup Roles & Customer
    role_buyer = Role(id=4, nama_role="Buyer", level=4)
    db.add(role_buyer)
    buyer = Buyer(
        id=1,
        name="Buyer Test",
        email="buyer@test.com",
        phone="081234567890",
        password_hash="mock",
        is_verified=True,
        is_active=True,
    )
    db.add(buyer)

    tepung = StockItem(
        id=10,
        nama_item="Tepung Terigu Premium",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        harga_per_satuan=Decimal("20.00"),
        stok_tersedia=Decimal("500.00"), # 500 gram
        version=0,
    )
    db.add(tepung)
    await db.commit()

    prod_a_id = 10
    prod_b_id = 11
    prod_c_id = 12

    # Product A: stock = 2 (needs 200g each, 500g available)
    prod_a = Product(
        id=prod_a_id,
        nama_produk="Kue Coklat",
        harga_jual=Decimal("50000.00"),
        hpp_total=Decimal("20000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
    )
    # Product B: depleted (needs 1000g each, 500g available -> stock = 0)
    prod_b = Product(
        id=prod_b_id,
        nama_produk="Kue Keju",
        harga_jual=Decimal("60000.00"),
        hpp_total=Decimal("25000.00"),
        is_active=True,
        is_available=True,
        minimum_order=1,
    )
    # Product C: manually unavailable by seller
    prod_c = Product(
        id=prod_c_id,
        nama_produk="Kue Tart",
        harga_jual=Decimal("120000.00"),
        hpp_total=Decimal("50000.00"),
        is_active=True,
        is_available=False,
        minimum_order=1,
    )
    db.add_all([prod_a, prod_b, prod_c])
    await db.commit()

    r_a = Recipe(product_id=prod_a_id, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("200.0000"))
    r_b = Recipe(product_id=prod_b_id, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("1000.0000"))
    r_c = Recipe(product_id=prod_c_id, stock_item_id=tepung.id, jumlah_dibutuhkan=Decimal("100.0000"))
    db.add_all([r_a, r_b, r_c])
    await db.commit()

    # Case 1: Mencoba memesan produk yang stoknya habis (Product B) -> MUST 400
    with pytest.raises(HTTPException) as exc_info:
        await order_service.create_new_order(
            db=db,
            customer_id=1,
            items=[{"product_id": prod_b_id, "jumlah": 1}],
            metode_pengiriman="pickup",
        )
    assert exc_info.value.status_code == 400
    assert "Stok produk 'Kue Keju' sedang habis" in exc_info.value.detail

    # Case 2: Mencoba memesan produk yang manual seller unavailable (Product C) -> MUST 400
    with pytest.raises(HTTPException) as exc_info:
        await order_service.create_new_order(
            db=db,
            customer_id=1,
            items=[{"product_id": prod_c_id, "jumlah": 1}],
            metode_pengiriman="pickup",
        )
    assert exc_info.value.status_code == 400
    assert "sedang tidak tersedia" in exc_info.value.detail

    # Case 3: Mencoba memesan kuantitas melebihi stok (Product A has stock 2, order 3) -> MUST 400
    with pytest.raises(HTTPException) as exc_info:
        await order_service.create_new_order(
            db=db,
            customer_id=1,
            items=[{"product_id": prod_a_id, "jumlah": 3}],
            metode_pengiriman="pickup",
        )
    assert exc_info.value.status_code == 400
    assert "melebihi stok yang tersedia" in exc_info.value.detail

    # Case 4: Memesan kuantitas yang valid (Product A, order 2 <= stock 2) -> SUCCEED
    order = await order_service.create_new_order(
        db=db,
        customer_id=1,
        items=[{"product_id": prod_a_id, "jumlah": 2}],
        metode_pengiriman="pickup",
    )
    assert order.id is not None
    assert order.total_harga_pesanan == Decimal("100000.00")

    # Verify stock reduction: 500g - (2 * 200g) = 100g remaining
    fresh_stock = (await db.execute(select(StockItem).where(StockItem.id == tepung.id))).scalars().first()
    assert fresh_stock.stok_tersedia == Decimal("100.00")


@pytest.mark.asyncio
async def test_api_products_endpoint_integration():
    """Test 4: Integrasi HTTP API GET /products dan GET /products/{id}."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with TestSessionLocal() as db:
        item = StockItem(
            id=1,
            nama_item="Coklat Bubuk",
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("50.00"),
            stok_tersedia=Decimal("300.00"),
            version=0,
        )
        db.add(item)
        p_in = Product(
            id=1,
            nama_produk="Brownies Kukus",
            harga_jual=Decimal("45000.00"),
            hpp_total=Decimal("15000.00"),
            is_active=True,
            is_available=True,
            minimum_order=1,
        )
        p_out = Product(
            id=2,
            nama_produk="Brownies Panggang",
            harga_jual=Decimal("55000.00"),
            hpp_total=Decimal("20000.00"),
            is_active=True,
            is_available=True,
            minimum_order=1,
        )
        db.add_all([p_in, p_out])
        await db.commit()

        # Recipe: Brownies Kukus needs 100g (3 available), Brownies Panggang needs 500g (0 available)
        r_in = Recipe(product_id=1, stock_item_id=1, jumlah_dibutuhkan=Decimal("100.0000"))
        r_out = Recipe(product_id=2, stock_item_id=1, jumlah_dibutuhkan=Decimal("500.0000"))
        db.add_all([r_in, r_out])
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # GET /products/ default -> returns both
        res = await client.get("/products/")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        p1 = next(x for x in data if x["id"] == 1)
        p2 = next(x for x in data if x["id"] == 2)
        assert p1["stock_quantity"] == 3
        assert p1["is_in_stock"] is True
        assert p2["stock_quantity"] == 0
        assert p2["is_in_stock"] is False

        # GET /products/?only_available=true -> returns only p1
        res_filter = await client.get("/products/?only_available=true")
        assert res_filter.status_code == 200
        data_filter = res_filter.json()
        assert len(data_filter) == 1
        assert data_filter[0]["id"] == 1

        # GET /products/2 -> single product detail with out-of-stock info
        res_detail = await client.get("/products/2")
        assert res_detail.status_code == 200
        detail_data = res_detail.json()
        assert detail_data["id"] == 2
        assert detail_data["stock_quantity"] == 0
        assert detail_data["is_in_stock"] is False

    app.dependency_overrides.clear()
    await test_engine.dispose()
