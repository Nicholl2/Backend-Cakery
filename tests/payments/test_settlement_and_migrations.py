import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.sql.dml import Update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.migrations import ensure_order_status_enum
from app.models.customer import Customer
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem, StockMovement
from app.services.payment_service import _apply_transaction_status, check_expired_settlements


@pytest.mark.asyncio
async def test_ensure_order_status_enum():
    """Verify ensure_order_status_enum behaves correctly on non-postgres and postgres dialects."""
    # 1. Non-postgres (e.g. SQLite) -> skips gracefully
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn:
        await ensure_order_status_enum(conn)

    async with engine.begin() as conn:
        await ensure_order_status_enum(conn)
    await engine.dispose()

    # 2. Mock PostgreSQL dialect in autocommit mode
    mock_conn = AsyncMock()
    mock_conn.dialect.name = "postgresql"

    mock_autocommit_c = AsyncMock()
    mock_conn.execution_options.return_value = mock_autocommit_c

    await ensure_order_status_enum(mock_conn)
    mock_conn.execution_options.assert_called_once_with(isolation_level="AUTOCOMMIT")
    assert "ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'refunded';" in str(mock_autocommit_c.execute.call_args[0][0])


@pytest.mark.asyncio
async def test_ensure_payment_columns():
    """Ensure PostgreSQL receives idempotent DDL on repeated repair calls."""
    from app.core.migrations import ensure_payment_columns

    # 1. Non-postgres -> skips gracefully
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn:
        await ensure_payment_columns(conn)
    await engine.dispose()

    # 2. Mock PostgreSQL dialect
    mock_conn = AsyncMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.scalar.return_value = True
    index_exists = False
    index_definition = {
        "relkind": "i", "table_name": "payments", "access_method": "btree",
        "is_unique": True, "is_valid": True, "is_ready": True,
        "has_no_predicate": True, "has_no_expressions": True,
        "key_count": 1, "total_count": 1, "first_key": "pg_order_id",
    }

    async def execute_without_duplicates(statement):
        nonlocal index_exists
        statement = str(statement)
        if "pg_class idx" in statement:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(
                first=lambda: index_definition if index_exists else None,
            ))
        if "CREATE UNIQUE INDEX" in statement:
            index_exists = True
        return SimpleNamespace(all=lambda: [])

    mock_conn.execute.side_effect = execute_without_duplicates

    await ensure_payment_columns(mock_conn)
    # This mock verifies repeatable statements, not the physical PostgreSQL schema.
    await ensure_payment_columns(mock_conn)
    mock_conn.scalar.assert_awaited()
    assert "information_schema.columns" in str(mock_conn.scalar.await_args.args[0])
    executed_statements = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
    assert any("ALTER TABLE payments ADD COLUMN IF NOT EXISTS notes TEXT;" in s for s in executed_statements)
    assert any("ALTER TABLE payments ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ;" in s for s in executed_statements)
    assert any("ALTER TABLE payments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;" in s for s in executed_statements)
    assert any("ALTER TABLE payments ADD COLUMN IF NOT EXISTS pg_order_id VARCHAR(100);" in s for s in executed_statements)
    assert any(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_pg_order_id ON payments (pg_order_id);" in s
        for s in executed_statements
    )
    assert any("WHERE pg_order_id IS NOT NULL" in s and "HAVING count(*) > 1" in s for s in executed_statements)


@pytest.mark.asyncio
async def test_ensure_payment_columns_refuses_duplicate_midtrans_order_ids():
    from app.core.migrations import ensure_payment_columns

    mock_conn = AsyncMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.scalar.return_value = True

    async def execute_with_duplicates(statement):
        if "pg_class idx" in str(statement):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))
        return SimpleNamespace(all=lambda: [
            SimpleNamespace(pg_order_id="duplicate-order", cnt=2),
        ])

    mock_conn.execute.side_effect = execute_with_duplicates

    with pytest.raises(RuntimeError, match="duplicate non-NULL payments.pg_order_id") as exc_info:
        await ensure_payment_columns(mock_conn)

    assert "'duplicate-order' (2x)" in str(exc_info.value)
    executed_statements = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
    assert not any("CREATE UNIQUE INDEX" in statement for statement in executed_statements)


@pytest.mark.parametrize("index_overrides", [
    {"is_unique": False},
    {"first_key": "id"},
    {"table_name": "orders"},
])
@pytest.mark.asyncio
async def test_ensure_payment_columns_rejects_wrong_named_index_definition(index_overrides):
    from app.core.migrations import ensure_payment_columns

    mock_conn = AsyncMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.scalar.return_value = True
    wrong_index = {
        "relkind": "i", "table_name": "payments", "access_method": "btree",
        "is_unique": True, "is_valid": True, "is_ready": True,
        "has_no_predicate": True, "has_no_expressions": True,
        "key_count": 1, "total_count": 1, "first_key": "pg_order_id",
    }
    wrong_index.update(index_overrides)

    async def execute_with_wrong_index(statement):
        if "pg_class idx" in str(statement):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: wrong_index))
        return SimpleNamespace(all=lambda: [])

    mock_conn.execute.side_effect = execute_with_wrong_index

    with pytest.raises(RuntimeError, match="already exists but is not a valid unique btree"):
        await ensure_payment_columns(mock_conn)

    statements = [str(call[0][0]) for call in mock_conn.execute.call_args_list]
    assert not any("CREATE UNIQUE INDEX" in statement for statement in statements)
    assert not any("GROUP BY pg_order_id" in statement for statement in statements)


@pytest.mark.asyncio
async def test_ensure_payment_columns_fails_clearly_without_created_at():
    from app.core.migrations import ensure_payment_columns

    mock_conn = AsyncMock()
    mock_conn.dialect.name = "postgresql"
    mock_conn.scalar.return_value = False

    with pytest.raises(RuntimeError, match="required column payments.created_at is missing"):
        await ensure_payment_columns(mock_conn)

    mock_conn.scalar.assert_awaited_once()
    mock_conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_auto_migrations_calls_payment_schema_repair(monkeypatch):
    import app.core.migrations as migrations

    migration_names = (
        "ensure_product_columns", "ensure_buyer_columns", "ensure_stock_item_columns",
        "ensure_purchase_cancellation_columns",
        "ensure_stock_master_name_constraint", "ensure_recipe_columns", "ensure_otp_columns",
        "ensure_user_columns", "ensure_order_columns", "ensure_customer_columns", "ensure_fulfillment_columns",
        "ensure_order_accessories_table", "ensure_payment_columns", "ensure_review_columns",
        "ensure_trigram_and_schema_optimizations", "ensure_product_images_table",
        "ensure_notification_table", "ensure_review_images_table", "ensure_email_outbox_indexes",
    )
    replacements = {name: AsyncMock() for name in migration_names}
    for name, replacement in replacements.items():
        monkeypatch.setattr(migrations, name, replacement)

    await migrations.run_auto_migrations(SimpleNamespace())

    replacements["ensure_payment_columns"].assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_settlement_keeps_order_pending():
    """
    Verify that when payment reaches full settlement:
    - invoice.status updates to 'paid'
    - order.status REMAINS 'pending' (NOT auto-transitioned to in_process)
    """
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as db:
        customer = Customer(
            id=1,
            nama="Test Customer",
            nomor_wa="081234567890",
        )
        db.add(customer)
        await db.flush()

        order = Order(
            id=10,
            customer_id=customer.id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("150000.00"),
            created_via="buyer",
        )
        db.add(order)
        await db.flush()

        invoice = Invoice(
            id=20,
            order_id=order.id,
            nomor_invoice="INV-TEST-001",
            total_tagihan=Decimal("150000.00"),
            status=InvoiceStatusEnum.unpaid,
        )
        db.add(invoice)
        await db.flush()

        payment = Payment(
            id=30,
            invoice_id=invoice.id,
            jumlah_bayar=Decimal("150000.00"),
            payment_method="bca_va",
            payment_type=PaymentTypeEnum.final,
            payment_status=PaymentStatusEnum.pending,
        )
        db.add(payment)
        await db.commit()

        # Apply settlement (success with full amount)
        await _apply_transaction_status(
            db=db,
            payment=payment,
            payload={"transaction_status": "settlement"},
            commit=True,
        )

        # Refresh objects
        await db.refresh(invoice)
        await db.refresh(order)

        # Invoice must be paid
        assert invoice.status == InvoiceStatusEnum.paid, f"Expected paid, got {invoice.status}"
        # Order MUST remain pending (allows buyer refund before kitchen production)
        assert order.status == OrderStatusEnum.pending, f"Expected pending, got {order.status}"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_expired_settlement_orders_are_transactionally_isolated():
    """A failed order's partial stock restoration must not persist with a later order."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_factory() as db:
        customer = Customer(id=1, nama="Settlement Test", nomor_wa="081234567890")
        product_a = Product(id=101, nama_produk="Order A Product")
        product_b = Product(id=102, nama_produk="Order B Product")
        stock_a = StockItem(
            id=201, nama_item="Stock A", satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
            stok_tersedia=Decimal("10"), alert_min_stok=Decimal("0"), version=0,
        )
        stock_b = StockItem(
            id=202, nama_item="Stock B", satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
            stok_tersedia=Decimal("20"), alert_min_stok=Decimal("0"), version=0,
        )
        stock_c = StockItem(
            id=203, nama_item="Stock C", satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
            stok_tersedia=Decimal("30"), alert_min_stok=Decimal("0"), version=0,
        )
        stock_d = StockItem(
            id=204, nama_item="Stock D", satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku, harga_per_satuan=Decimal("1"),
            stok_tersedia=Decimal("40"), alert_min_stok=Decimal("0"), version=0,
        )
        order_a = Order(
            id=301, customer_id=1, status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("30"),
            settlement_due_date=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        order_b = Order(
            id=302, customer_id=1, status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("40"),
            settlement_due_date=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add_all([
            customer, product_a, product_b, stock_a, stock_b, stock_c, stock_d,
            order_a, order_b,
        ])
        await db.flush()
        db.add_all([
            Recipe(id=401, product_id=101, stock_item_id=201, jumlah_dibutuhkan=Decimal("1")),
            Recipe(id=402, product_id=101, stock_item_id=202, jumlah_dibutuhkan=Decimal("1")),
            Recipe(id=403, product_id=101, stock_item_id=203, jumlah_dibutuhkan=Decimal("1")),
            Recipe(id=404, product_id=102, stock_item_id=204, jumlah_dibutuhkan=Decimal("1")),
            OrderItem(id=501, order_id=301, product_id=101, jumlah=1, subtotal=Decimal("30")),
            OrderItem(id=502, order_id=302, product_id=102, jumlah=1, subtotal=Decimal("40")),
            Invoice(
                id=601, order_id=301, nomor_invoice="INV-SETTLEMENT-A",
                total_tagihan=Decimal("30"), status=InvoiceStatusEnum.partial,
            ),
            Invoice(
                id=602, order_id=302, nomor_invoice="INV-SETTLEMENT-B",
                total_tagihan=Decimal("40"), status=InvoiceStatusEnum.partial,
            ),
        ])
        db.add_all([
            StockMovement(stock_item_id=201, movement_type="ORDER", quantity=Decimal("-1"),
                          reference_type="order", reference_id=301, reason="fixture consumption"),
            StockMovement(stock_item_id=202, movement_type="ORDER", quantity=Decimal("-1"),
                          reference_type="order", reference_id=301, reason="fixture consumption"),
            StockMovement(stock_item_id=203, movement_type="ORDER", quantity=Decimal("-1"),
                          reference_type="order", reference_id=301, reason="fixture consumption"),
            StockMovement(stock_item_id=204, movement_type="ORDER", quantity=Decimal("-1"),
                          reference_type="order", reference_id=302, reason="fixture consumption"),
        ])
        await db.commit()

        original_execute = AsyncSession.execute
        stock_c_id = 203
        update_attempts: list[int] = []
        successful_updates: list[int] = []

        async def execute_with_stock_c_conflict(session, statement, *args, **kwargs):
            if isinstance(statement, Update) and statement.table.name == StockItem.__tablename__:
                params = statement.compile(dialect=test_engine.sync_engine.dialect).params
                stock_id = params.get("id_1")
                if stock_id is not None:
                    update_attempts.append(stock_id)
                    if stock_id == stock_c_id:
                        # Match production's optimistic-lock outcome: UPDATE executes but affects no row.
                        return SimpleNamespace(rowcount=0)
                    result = await original_execute(session, statement, *args, **kwargs)
                    if result.rowcount:
                        successful_updates.append(stock_id)
                    return result
            return await original_execute(session, statement, *args, **kwargs)

        with patch.object(AsyncSession, "execute", execute_with_stock_c_conflict):
            cancelled_count = await check_expired_settlements(db)

        await db.refresh(order_a)
        await db.refresh(order_b)
        for stock in (stock_a, stock_b, stock_c, stock_d):
            await db.refresh(stock)

        movement_result = await db.execute(
            select(StockMovement).where(
                StockMovement.reference_type == "order",
                StockMovement.reference_id.in_([301, 302]),
            )
        )
        movements = movement_result.scalars().all()
        order_a_movements = sorted(
            (movement.stock_item_id, Decimal(str(movement.quantity)))
            for movement in movements if movement.reference_id == 301
        )
        order_b_movements = sorted(
            (movement.stock_item_id, Decimal(str(movement.quantity)))
            for movement in movements if movement.reference_id == 302
        )

        actual = {
            "cancelled_count": cancelled_count,
            "order_a_status": order_a.status,
            "order_a_stock": (stock_a.stok_tersedia, stock_b.stok_tersedia, stock_c.stok_tersedia),
            "order_a_movements": order_a_movements,
            "order_b_status": order_b.status,
            "order_b_stock": stock_d.stok_tersedia,
            "order_b_movements": order_b_movements,
            "conflict_attempts": update_attempts.count(203),
            "successful_stock_updates": successful_updates,
        }
        expected = {
            "cancelled_count": 1,
            "order_a_status": OrderStatusEnum.pending,
            "order_a_stock": (Decimal("10"), Decimal("20"), Decimal("30")),
            "order_a_movements": [
                (201, Decimal("-1.0000")), (202, Decimal("-1.0000")), (203, Decimal("-1.0000")),
            ],
            "order_b_status": OrderStatusEnum.cancelled_settlement_expired,
            "order_b_stock": Decimal("41"),
            "order_b_movements": [(204, Decimal("-1.0000")), (204, Decimal("1.0000"))],
            "conflict_attempts": 3,
            "successful_stock_updates": [201, 202, 204],
        }
        assert actual == expected, "failed Order A must not persist partial stock restoration"

    await test_engine.dispose()
