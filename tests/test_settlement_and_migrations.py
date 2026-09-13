import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.migrations import ensure_order_status_enum
from app.models.customer import Customer
from app.models.order import Order, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.services.payment_service import _apply_transaction_status


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
