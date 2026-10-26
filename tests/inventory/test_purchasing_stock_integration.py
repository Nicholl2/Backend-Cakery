import pytest
from decimal import Decimal
from fastapi import HTTPException
from pydantic import ValidationError
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
from app.schemas.stock import StockCreate, StockUpdate, StockAdjustmentCreate
from app.services import stock_service


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

    with pytest.raises(HTTPException) as cancel_error:
        await purchasing_service.cancel_purchase(db_session, purchase.id, "Tidak jadi")
    assert cancel_error.value.status_code == 409
    with pytest.raises(HTTPException) as edit_items_error:
        await purchasing_service.update_purchase(db_session, purchase.id, PurchaseUpdate(
            items=[PurchaseItemCreate(stock_item_id=stock_item.id, jumlah=Decimal("1"),
                                      harga_total=Decimal("20000"))],
        ))
    assert edit_items_error.value.status_code == 409

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

    movements = await stock_service.get_stock_movements(db_session, stock_item.id)
    assert [m.movement_type for m in movements].count("PURCHASE") == 1

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
async def test_single_material_restocked_from_multiple_suppliers(db_session: AsyncSession):
    supplier_a = Supplier(id=10, nama_supplier="Supplier A")
    supplier_b = Supplier(id=11, nama_supplier="Supplier B")
    db_session.add_all([supplier_a, supplier_b])
    await db_session.commit()
    item = await stock_service.create_stock(db_session, StockCreate(
        nama_item="Tepung Terigu", satuan="kg", kategori="bahan_baku",
        harga_per_satuan=Decimal("10000"), stok_tersedia=Decimal("100"),
        alert_min_stok=Decimal("1")))
    assert len(await stock_service.get_stock_movements(db_session, item.id)) == 1
    for supplier, qty, price in [(supplier_a, "50", "10000"), (supplier_b, "50", "12000")]:
        po = await purchasing_service.create_purchase(db_session, PurchaseCreate(
            supplier_id=supplier.id, items=[PurchaseItemCreate(stock_item_id=item.id,
                jumlah=Decimal(qty), harga_satuan=Decimal(price))]), created_by_user_id=1)
        await purchasing_service.update_purchase(db_session, po.id, PurchaseUpdate(is_received=True))
    await db_session.refresh(item)
    assert item.stok_tersedia == Decimal("200.00")
    assert item.harga_per_satuan == Decimal("10500.0000")
    count = (await db_session.execute(select(StockItem))).scalars().all()
    assert len(count) == 1
    movements = await stock_service.get_stock_movements(db_session, item.id)
    assert [m.movement_type for m in movements].count("PURCHASE") == 2


@pytest.mark.asyncio
async def test_stock_adjustment_records_reason_actor_and_delta(db_session: AsyncSession):
    item = StockItem(id=30, nama_item="Gula untuk opname", satuan=SatuanEnum.kg,
                     kategori=KategoriEnum.bahan_baku, stok_tersedia=Decimal("10"),
                     harga_per_satuan=Decimal("5000"), version=0)
    db_session.add(item)
    await db_session.commit()
    adjusted = await stock_service.adjust_stock(db_session, item.id,
        StockAdjustmentCreate(quantity_difference=Decimal("-2"), reason="Bahan rusak"), user_id=7)
    assert adjusted.stok_tersedia == Decimal("8.00")
    movement = (await stock_service.get_stock_movements(db_session, item.id))[0]
    assert movement.movement_type == "ADJUSTMENT_OUT"
    assert movement.quantity == Decimal("-2")
    assert movement.reason == "Bahan rusak"
    assert movement.created_by == 7


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
        harga_per_satuan=Decimal("100.0000"),  
        version=1,
    )
    db_session.add(stock_item)

    product = Product(
        id=5,
        nama_produk="Brownies Panggang",
        harga_jual=Decimal("60000.00"),
        hpp_total=Decimal("20000.00"),  
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


@pytest.mark.asyncio
async def test_purchase_total_input_derives_base_unit_cost(db_session: AsyncSession):
    supplier = Supplier(id=40, nama_supplier="Supplier Box")
    item = StockItem(id=40, nama_item="Box Bento", satuan=SatuanEnum.pcs,
                     kategori=KategoriEnum.kemasan, stok_tersedia=Decimal("0"),
                     harga_per_satuan=Decimal("2500"), version=0)
    db_session.add_all([supplier, item])
    await db_session.commit()

    purchase = await purchasing_service.create_purchase(db_session, PurchaseCreate(
        supplier_id=supplier.id,
        items=[PurchaseItemCreate(stock_item_id=item.id, jumlah=Decimal("100"),
                                  harga_total=Decimal("250000"))],
    ), created_by_user_id=1)
    detail = await purchasing_service.get_purchase_or_404(db_session, purchase.id)
    assert detail.total_harga == Decimal("250000.00")
    assert detail.purchase_items[0].harga_satuan == Decimal("2500.0000")
    assert detail.purchase_items[0].harga_total == Decimal("250000.00")


@pytest.mark.asyncio
async def test_purchase_price_deviation_requires_explicit_confirmation(db_session: AsyncSession):
    supplier = Supplier(id=41, nama_supplier="Supplier Variance")
    item = StockItem(id=41, nama_item="Chocolate Box", satuan=SatuanEnum.pcs,
                     kategori=KategoriEnum.kemasan, stok_tersedia=Decimal("100"),
                     harga_per_satuan=Decimal("2500"), version=1)
    db_session.add_all([supplier, item])
    await db_session.commit()
    data = PurchaseCreate(supplier_id=supplier.id, items=[PurchaseItemCreate(
        stock_item_id=item.id, jumlah=Decimal("100"), harga_total=Decimal("25000000"),
    )])
    with pytest.raises(HTTPException) as exc_info:
        await purchasing_service.create_purchase(db_session, data, created_by_user_id=1)
    assert exc_info.value.status_code == 422
    assert "average cost saat ini" in exc_info.value.detail

    confirmed = PurchaseCreate(supplier_id=supplier.id, items=[PurchaseItemCreate(
        stock_item_id=item.id, jumlah=Decimal("100"), harga_total=Decimal("25000000"),
        confirm_cost_deviation=True,
    )])
    purchase = await purchasing_service.create_purchase(db_session, confirmed, created_by_user_id=1)
    detail = await purchasing_service.get_purchase_or_404(db_session, purchase.id)
    assert detail.purchase_items[0].harga_satuan == Decimal("250000.0000")
    assert detail.purchase_items[0].cost_deviation_confirmed is True


@pytest.mark.asyncio
async def test_pending_purchase_can_edit_items_and_recalculate_total(db_session: AsyncSession):
    supplier = Supplier(id=42, nama_supplier="Supplier Edit")
    first = StockItem(id=42, nama_item="Box Mini", satuan=SatuanEnum.pcs,
                      kategori=KategoriEnum.kemasan, stok_tersedia=Decimal("0"),
                      harga_per_satuan=Decimal("1000"), version=0)
    second = StockItem(id=43, nama_item="Box Bento", satuan=SatuanEnum.pcs,
                       kategori=KategoriEnum.kemasan, stok_tersedia=Decimal("0"),
                       harga_per_satuan=Decimal("2000"), version=0)
    db_session.add_all([supplier, first, second])
    await db_session.commit()
    purchase = await purchasing_service.create_purchase(db_session, PurchaseCreate(
        supplier_id=supplier.id, items=[PurchaseItemCreate(
            stock_item_id=first.id, jumlah=Decimal("5"), harga_total=Decimal("5000"),
        )],
    ), created_by_user_id=1)

    updated = await purchasing_service.update_purchase(db_session, purchase.id, PurchaseUpdate(
        items=[PurchaseItemCreate(stock_item_id=second.id, jumlah=Decimal("10"),
                                  harga_total=Decimal("25000"))],
        catatan="Koreksi jumlah dan item",
    ))
    detail = await purchasing_service.get_purchase_or_404(db_session, updated.id)
    assert detail.total_harga == Decimal("25000.00")
    assert len(detail.purchase_items) == 1
    assert detail.purchase_items[0].stock_item_id == second.id
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.stok_tersedia == Decimal("0")
    assert second.stok_tersedia == Decimal("0")


@pytest.mark.asyncio
async def test_purchase_rejects_base_unit_cost_outside_database_precision(db_session: AsyncSession):
    supplier = Supplier(id=45, nama_supplier="Supplier Invalid Cost")
    item = StockItem(id=45, nama_item="Bahan dengan biaya awal nol", satuan=SatuanEnum.gram,
                     kategori=KategoriEnum.bahan_baku, stok_tersedia=Decimal("0"),
                     harga_per_satuan=Decimal("0"), version=0)
    db_session.add_all([supplier, item])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await purchasing_service.create_purchase(db_session, PurchaseCreate(
            supplier_id=supplier.id,
            items=[PurchaseItemCreate(
                stock_item_id=item.id,
                jumlah=Decimal("0.0001"),
                harga_total=Decimal("100"),
            )],
        ), created_by_user_id=1)
    assert exc_info.value.status_code == 422
    assert "melewati batas" in exc_info.value.detail
    assert (await db_session.execute(select(Purchase))).scalars().all() == []


@pytest.mark.asyncio
async def test_cancelling_pending_purchase_is_audited_and_cannot_be_received(db_session: AsyncSession):
    supplier = Supplier(id=44, nama_supplier="Supplier Cancel")
    item = StockItem(id=44, nama_item="Brown Sugar", satuan=SatuanEnum.gram,
                     kategori=KategoriEnum.bahan_baku, stok_tersedia=Decimal("0"),
                     harga_per_satuan=Decimal("29"), version=0)
    db_session.add_all([supplier, item])
    await db_session.commit()
    purchase = await purchasing_service.create_purchase(db_session, PurchaseCreate(
        supplier_id=supplier.id, items=[PurchaseItemCreate(
            stock_item_id=item.id, jumlah=Decimal("1000"), harga_total=Decimal("29000"),
        )],
    ), created_by_user_id=1)

    cancelled = await purchasing_service.cancel_purchase(
        db_session, purchase.id, "Harga salah input", cancelled_by=12,
    )
    assert cancelled.is_cancelled is True
    assert cancelled.cancelled_by == 12
    assert cancelled.cancellation_reason == "Harga salah input"
    assert cancelled.cancelled_at is not None
    assert (await purchasing_service.get_all_purchases(
        db_session, only_cancelled=True
    ))[0].id == purchase.id
    assert await purchasing_service.get_all_purchases(
        db_session, only_received=False
    ) == []

    with pytest.raises(HTTPException) as receive_error:
        await purchasing_service.update_purchase(
            db_session, purchase.id, PurchaseUpdate(is_received=True),
        )
    assert receive_error.value.status_code == 409
    with pytest.raises(HTTPException) as edit_error:
        await purchasing_service.update_purchase(
            db_session, purchase.id, PurchaseUpdate(catatan="tidak boleh"),
        )
    assert edit_error.value.status_code == 409
    await db_session.refresh(item)
    assert item.stok_tersedia == Decimal("0")
    assert await stock_service.get_stock_movements(db_session, item.id) == []


def test_purchase_and_stock_update_validation():
    with pytest.raises(ValidationError):
        PurchaseItemCreate(stock_item_id=1, jumlah=Decimal("1"),
                           harga_satuan=Decimal("10"), harga_total=Decimal("10"))
    with pytest.raises(ValidationError):
        PurchaseItemCreate(stock_item_id=1, jumlah=Decimal("1"),
                           harga_total=Decimal("0"))
    with pytest.raises(ValidationError):
        PurchaseItemCreate(stock_item_id=1, jumlah=Decimal("1000000"),
                           harga_total=Decimal("1"))
    with pytest.raises(ValidationError):
        StockUpdate.model_validate({"harga_per_satuan": Decimal("2500")})
