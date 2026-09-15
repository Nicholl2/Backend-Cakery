import pytest
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.purchasing import Supplier, Purchase, PurchaseItem
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.user import User
from app.schemas.purchasing import PurchaseCreate, PurchaseItemCreate, PurchaseUpdate
from app.services import purchasing_service


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
async def test_purchase_received_updates_stock_and_weighted_average_cost(db_session: AsyncSession):
    """
    Test that marking a purchase order as received:
    1. Increases StockItem.stok_tersedia by received quantity.
    2. Updates StockItem.harga_per_satuan using Weighted Average Costing.
    3. Increments StockItem.version.
    4. Automatically sets purchase.tanggal_diterima.
    """
    # 1. Setup user & supplier
    user = User(
        id=1,
        username="staff_baker",
        role_id=1,
        password_hash="fakehash",
    )
    db_session.add(user)

    supplier = Supplier(
        id=1,
        nama_supplier="Toko Bahan Kue Makmur",
        kontak_person="Makmur",
    )
    db_session.add(supplier)

    # 2. Setup StockItem: 50 kg at Rp 10,000/kg (Total inventory value = Rp 500,000)
    stock_item = StockItem(
        id=1,
        nama_item="Tepung Terigu Protein Sedang",
        satuan=SatuanEnum.kg,
        kategori=KategoriEnum.bahan_baku,
        stok_tersedia=Decimal("50.00"),
        harga_per_satuan=Decimal("10000.0000"),
        supplier_id=supplier.id,
        version=1,
    )
    db_session.add(stock_item)
    await db_session.commit()

    # 3. Create Purchase Order: Buy 50 kg at Rp 20,000/kg (Total cost = Rp 1,000,000)
    purchase_data = PurchaseCreate(
        supplier_id=supplier.id,
        nomor_po="PO-TEST-001",
        catatan="Beli tepung terigu",
        items=[
            PurchaseItemCreate(
                stock_item_id=stock_item.id,
                jumlah=Decimal("50.0000"),
                harga_satuan=Decimal("20000.0000"),
            )
        ],
    )
    purchase = await purchasing_service.create_purchase(db_session, purchase_data, created_by_user_id=user.id)
    assert purchase.is_received is False
    assert purchase.tanggal_diterima is None

    # StockItem should NOT be affected yet (purchase not received)
    await db_session.refresh(stock_item)
    assert stock_item.stok_tersedia == Decimal("50.00")
    assert stock_item.harga_per_satuan == Decimal("10000.0000")

    # 4. Mark purchase as received
    updated_purchase = await purchasing_service.update_purchase(
        db_session,
        purchase.id,
        PurchaseUpdate(is_received=True),
    )

    assert updated_purchase.is_received is True
    assert updated_purchase.tanggal_diterima is not None

    # 5. Verify StockItem:
    # Expected stok_tersedia = 50 + 50 = 100.00
    # Expected average cost = (50 * 10,000 + 50 * 20,000) / 100 = 1,500,000 / 100 = 15,000.0000
    await db_session.refresh(stock_item)
    assert stock_item.stok_tersedia == Decimal("100.00")
    assert stock_item.harga_per_satuan == Decimal("15000.0000")
    assert stock_item.version == 2


@pytest.mark.asyncio
async def test_purchase_receive_protections(db_session: AsyncSession):
    """
    Test idempotency and protection guards:
    1. Un-receiving an already received purchase raises 409 CONFLICT.
    2. Updating another field on an already received purchase does not double-add stock.
    """
    supplier = Supplier(id=2, nama_supplier="Supplier Mentega Gurih")
    db_session.add(supplier)

    stock_item = StockItem(
        id=2,
        nama_item="Mentega Wisman",
        satuan=SatuanEnum.kg,
        kategori=KategoriEnum.bahan_baku,
        stok_tersedia=Decimal("10.00"),
        harga_per_satuan=Decimal("50000.0000"),
        supplier_id=supplier.id,
        version=1,
    )
    db_session.add(stock_item)
    await db_session.commit()

    purchase_data = PurchaseCreate(
        supplier_id=supplier.id,
        nomor_po="PO-TEST-002",
        items=[
            PurchaseItemCreate(
                stock_item_id=stock_item.id,
                jumlah=Decimal("10.0000"),
                harga_satuan=Decimal("60000.0000"),
            )
        ],
    )
    purchase = await purchasing_service.create_purchase(db_session, purchase_data, created_by_user_id=1)

    # Receive purchase
    await purchasing_service.update_purchase(db_session, purchase.id, PurchaseUpdate(is_received=True))

    await db_session.refresh(stock_item)
    assert stock_item.stok_tersedia == Decimal("20.00")
    assert stock_item.harga_per_satuan == Decimal("55000.0000")

    # 1. Attempt to set is_received = False -> must raise HTTP 409
    with pytest.raises(HTTPException) as exc_info:
        await purchasing_service.update_purchase(
            db_session,
            purchase.id,
            PurchaseUpdate(is_received=False),
        )
    assert exc_info.value.status_code == 409

    # 2. Update another field (e.g. catatan) -> must NOT double count stock!
    await purchasing_service.update_purchase(
        db_session,
        purchase.id,
        PurchaseUpdate(catatan="Catatan diperbarui"),
    )

    await db_session.refresh(stock_item)
    assert stock_item.stok_tersedia == Decimal("20.00")
    assert stock_item.harga_per_satuan == Decimal("55000.0000")


@pytest.mark.asyncio
async def test_product_hpp_recalculation_on_purchase_received(db_session: AsyncSession):
    """
    Test that when a purchase is received, product HPP total for recipe products is recalculated.
    """
    supplier = Supplier(id=3, nama_supplier="Supplier Cokelat Murni")
    db_session.add(supplier)

    stock_item = StockItem(
        id=3,
        nama_item="Dark Chocolate Compound",
        satuan=SatuanEnum.gram,
        kategori=KategoriEnum.bahan_baku,
        stok_tersedia=Decimal("1000.00"),
        harga_per_satuan=Decimal("100.0000"),  # Rp 100 / gram
        supplier_id=supplier.id,
        version=1,
    )
    db_session.add(stock_item)

    product = Product(
        id=5,
        nama_produk="Brownies Panggang",
        harga_jual=Decimal("60000.00"),
        hpp_total=Decimal("20000.00"),  # 200g * 100 = 20,000
    )
    db_session.add(product)
    await db_session.flush()

    recipe = Recipe(
        product_id=product.id,
        stock_item_id=stock_item.id,
        jumlah_dibutuhkan=Decimal("200.00"),
    )
    db_session.add(recipe)
    await db_session.commit()

    # Buy 1000g at Rp 200 / gram (Higher price)
    purchase_data = PurchaseCreate(
        supplier_id=supplier.id,
        nomor_po="PO-TEST-003",
        items=[
            PurchaseItemCreate(
                stock_item_id=stock_item.id,
                jumlah=Decimal("1000.0000"),
                harga_satuan=Decimal("200.0000"),
            )
        ],
    )
    purchase = await purchasing_service.create_purchase(db_session, purchase_data, created_by_user_id=1)

    # Receive purchase:
    # New stock = 2000g
    # New average cost = (1000 * 100 + 1000 * 200) / 2000 = 300,000 / 2000 = Rp 150 / gram
    await purchasing_service.update_purchase(db_session, purchase.id, PurchaseUpdate(is_received=True))

    await db_session.refresh(stock_item)
    assert stock_item.stok_tersedia == Decimal("2000.00")
    assert stock_item.harga_per_satuan == Decimal("150.0000")

    # Product HPP should now be 200g * 150 = 30,000!
    await db_session.refresh(product)
    assert product.hpp_total == Decimal("30000.00")
