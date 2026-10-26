from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.expense import Expense, ExpenseAudit
from app.models.customer import Customer
from app.models.order import (
    Invoice,
    InvoiceStatusEnum,
    MetodePengirimanEnum,
    Order,
    OrderItem,
    OrderStatusEnum,
)
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.role import Role
from app.models.user import User
from app.schemas.expense import ExpenseCancel, ExpenseCreate, ExpenseUpdate
from app.services import expense_service, report_service


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_expense_create_validation_and_normalization():
    payload = ExpenseCreate(
        kategori="  Listrik  ",
        jumlah="125000.50",
        deskripsi="  Tagihan dapur  ",
        metode_pembayaran="bank_transfer",
        bukti_url="https://files.example.test/receipt/1",
    )
    assert payload.kategori == "Listrik"
    assert payload.deskripsi == "Tagihan dapur"
    assert payload.jumlah == Decimal("125000.50")

    with pytest.raises(ValidationError):
        ExpenseCreate(kategori="AB", jumlah="1")
    with pytest.raises(ValidationError):
        ExpenseCreate(kategori="Listrik", jumlah="0")
    with pytest.raises(ValidationError):
        ExpenseCreate(kategori="Listrik", jumlah="1", metode_pembayaran="crypto")
    with pytest.raises(ValidationError):
        ExpenseCreate(kategori="Listrik", jumlah="1", bukti_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        ExpenseCreate(kategori="Listrik", jumlah="1", bukti_url="http://files.example.test/receipt")


def test_expense_update_requires_real_editable_fields():
    assert ExpenseUpdate(jumlah="20").jumlah == Decimal("20")
    assert ExpenseUpdate(deskripsi=None).model_fields_set == {"deskripsi"}
    with pytest.raises(ValidationError):
        ExpenseUpdate()
    with pytest.raises(ValidationError):
        ExpenseUpdate(jumlah=None)
    with pytest.raises(ValidationError):
        ExpenseUpdate(tanggal=None)
    with pytest.raises(ValidationError):
        ExpenseCancel(reason="  ")


@pytest.mark.asyncio
async def test_expense_create_edit_void_audit_and_summary(db_session):
    role = Role(nama_role="Owner", level=1)
    db_session.add(role)
    await db_session.flush()
    user = User(username="expense-owner", password_hash="not-used", role_id=role.id)
    db_session.add(user)
    await db_session.flush()

    created = await expense_service.create_expense(
        db_session,
        ExpenseCreate(kategori="Listrik", jumlah="120000", deskripsi="Awal"),
        recorded_by=user.id,
    )
    assert created.is_voided is False
    assert created.recorded_by == user.id

    edited = await expense_service.update_expense(
        db_session,
        created.id,
        ExpenseUpdate(jumlah="125000", deskripsi="Revisi tagihan"),
        updated_by=user.id,
    )
    assert edited.jumlah == Decimal("125000")
    events = await expense_service.get_expense_audit(db_session, created.id)
    assert [event.action for event in events] == ["created", "updated"]
    assert events[1].changes["jumlah"] == {"before": "120000.00", "after": "125000"}

    voided = await expense_service.void_expense(db_session, created.id, "Salah catat", user.id)
    assert voided.is_voided is True
    assert voided.void_reason == "Salah catat"
    with pytest.raises(HTTPException) as edit_cancelled:
        await expense_service.update_expense(db_session, created.id, ExpenseUpdate(jumlah="1"), user.id)
    assert edit_cancelled.value.status_code == 409
    with pytest.raises(HTTPException) as cancel_again:
        await expense_service.void_expense(db_session, created.id, "Ulang", user.id)
    assert cancel_again.value.status_code == 409

    await expense_service.create_expense(
        db_session,
        ExpenseCreate(
            kategori="Listrik",
            jumlah="50000",
            tanggal=datetime(2026, 2, 15, tzinfo=timezone.utc),
        ),
        recorded_by=user.id,
    )
    summary = await expense_service.get_expenses_summary(
        db_session,
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc),
    )
    assert summary["total_expenses"] == Decimal("50000.00")
    assert summary["count"] == 1
    assert summary["by_category"] == {"Listrik": Decimal("50000.00")}

    summary = await expense_service.get_expenses_summary(
        db_session,
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc),
    )
    assert summary["total_expenses"] == Decimal("0.00")
    assert summary["count"] == 0
    assert summary["by_category"] == {}

    audit_rows = await db_session.execute(select(ExpenseAudit).where(ExpenseAudit.expense_id == created.id))
    assert len(audit_rows.scalars().all()) == 3


@pytest.mark.asyncio
async def test_sales_trends_buckets_and_fills_empty_days():
    rows = [
        SimpleNamespace(
            settled_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
            revenue=Decimal("100000"),
            hpp=Decimal("40000"),
        ),
        SimpleNamespace(
            settled_at=datetime(2026, 10, 3, 9, tzinfo=timezone.utc),
            revenue=Decimal("50000"),
            hpp=Decimal("20000"),
        ),
    ]
    with patch("app.services.report_service.report_repo.get_sales_trend_rows", new=AsyncMock(return_value=rows)):
        report = await report_service.get_sales_trends(None, "2026-10-01", "2026-10-03", "day")

    assert len(report.points) == 3
    assert report.points[0].revenue == Decimal("100000")
    assert report.points[1].revenue == Decimal("0.00")
    assert report.points[1].order_count == 0
    assert report.points[2].gross_profit == Decimal("30000")


@pytest.mark.asyncio
async def test_sales_trends_rejects_invalid_or_unbounded_ranges():
    with pytest.raises(HTTPException) as invalid_order:
        await report_service.get_sales_trends(None, "2026-10-10", "2026-10-01", "day")
    assert getattr(invalid_order.value, "status_code", None) == 400

    with pytest.raises(HTTPException) as too_long:
        await report_service.get_sales_trends(None, "2025-01-01", "2026-12-31", "day")
    assert getattr(too_long.value, "status_code", None) == 422


@pytest.mark.asyncio
async def test_sales_trends_match_financial_report_paid_order_totals(db_session):
    customer = Customer(nama="Trend fixture", nomor_wa="+628123450000")
    db_session.add(customer)
    await db_session.flush()

    settled_at = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
    order = Order(
        customer_id=customer.id,
        status=OrderStatusEnum.completed,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("150000"),
        created_at=settled_at,
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        OrderItem(
            order_id=order.id,
            product_id=None,
            custom_product_name="Trend cake",
            jumlah=2,
            subtotal=Decimal("150000"),
            hpp_snapshot=Decimal("30000"),
        )
    )
    invoice = Invoice(
        order_id=order.id,
        nomor_invoice="TREND-PAID-1",
        total_tagihan=Decimal("150000"),
        status=InvoiceStatusEnum.paid,
    )
    db_session.add(invoice)
    await db_session.flush()
    db_session.add(
        Payment(
            invoice_id=invoice.id,
            jumlah_bayar=Decimal("150000"),
            payment_method="cash",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.final,
            created_at=settled_at,
            settled_at=settled_at,
        )
    )
    await db_session.commit()

    trends = await report_service.get_sales_trends(db_session, "2026-10-01", "2026-10-07", "day")
    finance = await report_service.get_financial_report(db_session, "2026-10-01", "2026-10-07")
    assert sum(point.revenue for point in trends.points) == finance.total_revenue == Decimal("150000.00")
    assert sum(point.hpp for point in trends.points) == finance.total_hpp_cost == Decimal("60000.00")
    assert sum(point.gross_profit for point in trends.points) == finance.gross_profit == Decimal("90000.00")
    assert sum(point.order_count for point in trends.points) == 1
