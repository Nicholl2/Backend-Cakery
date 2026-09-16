import pytest
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.customer import Customer
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.product import Product
from app.models.purchasing import Supplier, Purchase
from app.models.expense import Expense
from app.services import report_service


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
async def test_financial_report_date_consistency_and_unpaid_exclusion(db_session: AsyncSession):
    """
    Test that:
    1. An order created in Jan 2026 but paid in Feb 2026 allocates both Revenue and HPP to Feb 2026.
    2. An unpaid order in Feb 2026 is completely excluded from Revenue, HPP, Gross Profit, and Net Profit.
    3. Outstanding payments correctly sums unpaid and partial invoice balances.
    4. Full product profitability and supplier spending return expected structured metrics.
    """
    # 1. Setup Customer & Product
    customer = Customer(id=1, nama="Budi Pembeli", nomor_wa="081299990001")
    db_session.add(customer)

    product1 = Product(
        id=1,
        nama_produk="Kue Tart Cokelat",
        harga_jual=Decimal("150000.00"),
        hpp_total=Decimal("80000.00"),
    )
    product2 = Product(
        id=2,
        nama_produk="Bolu Pandan",
        harga_jual=Decimal("60000.00"),
        hpp_total=Decimal("30000.00"),
    )
    db_session.add_all([product1, product2])
    await db_session.flush()

    # 2. Order #1: Created Jan 28, 2026. Paid Feb 3, 2026.
    jan_date = datetime(2026, 1, 28, 10, 0, 0, tzinfo=timezone.utc)
    feb_date_order1_paid = datetime(2026, 2, 3, 14, 30, 0, tzinfo=timezone.utc)

    order1 = Order(
        id=101,
        customer_id=customer.id,
        status=OrderStatusEnum.in_process,
        metode_pengiriman=MetodePengirimanEnum.delivery,
        total_harga_pesanan=Decimal("150000.00"),
        created_at=jan_date,
    )
    db_session.add(order1)
    await db_session.flush()

    item1 = OrderItem(
        order_id=order1.id,
        product_id=product1.id,
        jumlah=1,
        subtotal=Decimal("150000.00"),
        hpp_snapshot=Decimal("80000.00"),
    )
    db_session.add(item1)

    inv1 = Invoice(
        id=201,
        order_id=order1.id,
        nomor_invoice="INV-JAN-001",
        total_tagihan=Decimal("150000.00"),
        status=InvoiceStatusEnum.paid,
        created_at=jan_date,
    )
    db_session.add(inv1)
    await db_session.flush()

    pay1 = Payment(
        id=301,
        invoice_id=inv1.id,
        jumlah_bayar=Decimal("150000.00"),
        payment_method="bca_va",
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.final,
        created_at=feb_date_order1_paid,
        settled_at=feb_date_order1_paid,
    )
    db_session.add(pay1)

    # 3. Order #2: Created Feb 10, 2026. UNPAID / Pending Payment!
    feb_date_order2 = datetime(2026, 2, 10, 11, 0, 0, tzinfo=timezone.utc)
    order2 = Order(
        id=102,
        customer_id=customer.id,
        status=OrderStatusEnum.pending,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("120000.00"),
        created_at=feb_date_order2,
    )
    db_session.add(order2)
    await db_session.flush()

    item2 = OrderItem(
        order_id=order2.id,
        product_id=product2.id,
        jumlah=2,
        subtotal=Decimal("120000.00"),
        hpp_snapshot=Decimal("30000.00"),  # Total HPP = 60,000
    )
    db_session.add(item2)

    inv2 = Invoice(
        id=202,
        order_id=order2.id,
        nomor_invoice="INV-FEB-UNPAID",
        total_tagihan=Decimal("120000.00"),
        status=InvoiceStatusEnum.unpaid,
        created_at=feb_date_order2,
    )
    db_session.add(inv2)

    # 4. Add an Expense in Feb 2026
    expense = Expense(
        kategori="Listrik & Air",
        jumlah=Decimal("20000.00"),
        recorded_by=1,
        tanggal=datetime(2026, 2, 15, 9, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(expense)

    # 5. Add Supplier & Purchase in Feb 2026
    supplier = Supplier(
        id=1,
        nama_supplier="Supplier Tepung Jaya",
        kontak_person="Pak Jaya",
    )
    db_session.add(supplier)
    await db_session.flush()

    purchase = Purchase(
        supplier_id=supplier.id,
        created_by=1,
        nomor_po="PO-FEB-001",
        tanggal_pemesanan=datetime(2026, 2, 5, 8, 0, 0, tzinfo=timezone.utc),
        total_harga=Decimal("500000.00"),
        is_received=True,
    )
    db_session.add(purchase)

    await db_session.commit()

    # ── Test January 2026 Report ─────────────────────────────────────────────
    # Order #1 was created in Jan, but paid in Feb -> Jan must have 0 revenue and 0 HPP!
    jan_report = await report_service.get_financial_report(db_session, "2026-01-01", "2026-01-31")
    assert jan_report.total_revenue == Decimal("0.00")
    assert jan_report.revenue == Decimal("0.00")
    assert jan_report.cash_received == Decimal("0.00")
    assert jan_report.total_hpp_cost == Decimal("0.00")
    assert jan_report.hpp_total == Decimal("0.00")
    assert jan_report.gross_profit == Decimal("0.00")
    assert jan_report.net_profit == Decimal("0.00")
    assert jan_report.non_refundable_dp_income == Decimal("0.00")

    # ── Test February 2026 Report ────────────────────────────────────
    # Order #1 settlement occurred in Feb -> Revenue = 150,000, HPP = 80,000
    # Order #2 is UNPAID -> completely excluded from Revenue, HPP, Gross Profit!
    # Expenses = 20,000
    # Gross Profit = 150,000 - 80,000 = 70,000
    # Net Profit = 70,000 - 20,000 = 50,000
    feb_report = await report_service.get_financial_report(db_session, "2026-02-01", "2026-02-28")

    assert feb_report.total_revenue == Decimal("150000.00")
    assert feb_report.revenue == Decimal("150000.00")
    assert feb_report.cash_received == Decimal("150000.00")
    assert feb_report.total_hpp_cost == Decimal("80000.00")
    assert feb_report.hpp_total == Decimal("80000.00")
    assert feb_report.total_expenses == Decimal("20000.00")
    assert feb_report.expenses_total == Decimal("20000.00")
    assert feb_report.gross_profit == Decimal("70000.00")
    assert feb_report.net_profit == Decimal("50000.00")
    assert feb_report.non_refundable_dp_income == Decimal("0.00")

    # Outstanding payments must include Order #2 (120,000 unpaid)
    assert feb_report.outstanding_payments == Decimal("120000.00")

    # Full product profitability must only include settled products (Kue Tart Cokelat, not Bolu Pandan)
    assert len(feb_report.full_product_profitability) == 1
    assert len(feb_report.product_profitability) == 1
    prof_item = feb_report.full_product_profitability[0]
    assert prof_item.nama_produk == "Kue Tart Cokelat"
    assert prof_item.qty_sold == 1
    assert prof_item.total_revenue == Decimal("150000.00")
    assert prof_item.total_hpp == Decimal("80000.00")
    assert prof_item.gross_profit == Decimal("70000.00")
    assert prof_item.margin_percentage == pytest.approx(46.67, abs=0.01)

    # Supplier spending
    assert len(feb_report.supplier_spending) == 1
    sup_item = feb_report.supplier_spending[0]
    assert sup_item.nama_supplier == "Supplier Tepung Jaya"
    assert sup_item.total_spending == Decimal("500000.00")
    assert sup_item.purchase_count == 1


@pytest.mark.asyncio
async def test_partial_payment_and_cancelled_orders_handling(db_session: AsyncSession):
    """
    Verify that:
    1. Partially paid orders (DP paid, remaining unpaid) are NOT counted in Revenue & HPP (not fully settled).
    2. Outstanding payments correctly calculates (total_tagihan - partial_paid).
    3. Cancelled and refunded orders are completely excluded from outstanding payments, revenue, and HPP.
    """
    customer = Customer(id=2, nama="Siti Pembeli", nomor_wa="081299990002")
    db_session.add(customer)

    product = Product(
        id=10,
        nama_produk="Cupcake Red Velvet",
        harga_jual=Decimal("25000.00"),
        hpp_total=Decimal("12000.00"),
    )
    db_session.add(product)
    await db_session.flush()

    report_date = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Partial Order: Total 200,000. DP of 60,000 paid. Remaining 140,000.
    partial_order = Order(
        id=301,
        customer_id=customer.id,
        status=OrderStatusEnum.in_process,
        metode_pengiriman=MetodePengirimanEnum.delivery,
        total_harga_pesanan=Decimal("200000.00"),
        created_at=report_date,
    )
    db_session.add(partial_order)
    await db_session.flush()

    partial_item = OrderItem(
        order_id=partial_order.id,
        product_id=product.id,
        jumlah=8,
        subtotal=Decimal("200000.00"),
        hpp_snapshot=Decimal("12000.00"),
    )
    db_session.add(partial_item)

    partial_inv = Invoice(
        id=401,
        order_id=partial_order.id,
        nomor_invoice="INV-PARTIAL-001",
        total_tagihan=Decimal("200000.00"),
        status=InvoiceStatusEnum.partial,
        created_at=report_date,
    )
    db_session.add(partial_inv)
    await db_session.flush()

    dp_payment = Payment(
        id=501,
        invoice_id=partial_inv.id,
        jumlah_bayar=Decimal("60000.00"),
        payment_method="bca_va",
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.dp,
        created_at=report_date,
        settled_at=report_date,
    )
    db_session.add(dp_payment)

    # 2. Cancelled Order: Total 100,000. Invoice unpaid.
    cancelled_order = Order(
        id=302,
        customer_id=customer.id,
        status=OrderStatusEnum.cancelled,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("100000.00"),
        created_at=report_date,
    )
    db_session.add(cancelled_order)
    await db_session.flush()

    cancelled_inv = Invoice(
        id=402,
        order_id=cancelled_order.id,
        nomor_invoice="INV-CANCELLED-001",
        total_tagihan=Decimal("100000.00"),
        status=InvoiceStatusEnum.unpaid,
        created_at=report_date,
    )
    db_session.add(cancelled_inv)

    await db_session.commit()

    report = await report_service.get_financial_report(db_session, "2026-03-01", "2026-03-31")

    # Partial order is not fully settled (InvoiceStatusEnum.paid), so Revenue and HPP are 0
    assert report.total_revenue == Decimal("0.00")
    assert report.revenue == Decimal("0.00")
    assert report.total_hpp_cost == Decimal("0.00")
    assert report.hpp_total == Decimal("0.00")
    assert report.gross_profit == Decimal("0.00")

    # Cash received must capture the DP paid in March (60,000)
    assert report.cash_received == Decimal("60000.00")
    assert report.non_refundable_dp_income == Decimal("0.00")

    # Outstanding payments must be 140,000 (200,000 - 60,000 DP).
    # Cancelled order (100,000) must be excluded completely!
    assert report.outstanding_payments == Decimal("140000.00")


@pytest.mark.asyncio
async def test_cumulative_outstanding_and_non_refundable_dp(db_session: AsyncSession):
    """
    Test that:
    1. Cumulative outstanding payments includes unpaid/partial orders from prior periods up to end_date.
    2. Cancelled orders with successful non-refunded DP are counted in non_refundable_dp_income and cash_received.
    3. Non-refundable DP is factored into net_profit (net_profit = gross_profit - expenses + non_refundable_dp).
    4. Refunded orders/payments are excluded from non_refundable_dp_income and outstanding_payments.
    """
    customer = Customer(id=3, nama="Dewi Pembeli", nomor_wa="081299990003")
    db_session.add(customer)

    jan_date = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    feb_date_1 = datetime(2026, 2, 10, 9, 0, 0, tzinfo=timezone.utc)
    feb_date_2 = datetime(2026, 2, 12, 14, 0, 0, tzinfo=timezone.utc)
    feb_date_3 = datetime(2026, 2, 18, 16, 0, 0, tzinfo=timezone.utc)

    # 1. Order #1 (Created in January, STILL UNPAID): Total 100,000
    order_jan = Order(
        id=601,
        customer_id=customer.id,
        status=OrderStatusEnum.pending,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("100000.00"),
        created_at=jan_date,
    )
    db_session.add(order_jan)
    await db_session.flush()

    inv_jan = Invoice(
        id=701,
        order_id=order_jan.id,
        nomor_invoice="INV-JAN-OLD",
        total_tagihan=Decimal("100000.00"),
        status=InvoiceStatusEnum.unpaid,
        created_at=jan_date,
    )
    db_session.add(inv_jan)

    # 2. Order #2 (Created in February, PARTIAL with DP 30,000 paid): Total 80,000, Remaining 50,000
    order_feb_partial = Order(
        id=602,
        customer_id=customer.id,
        status=OrderStatusEnum.in_process,
        metode_pengiriman=MetodePengirimanEnum.delivery,
        total_harga_pesanan=Decimal("80000.00"),
        created_at=feb_date_1,
    )
    db_session.add(order_feb_partial)
    await db_session.flush()

    inv_feb_partial = Invoice(
        id=702,
        order_id=order_feb_partial.id,
        nomor_invoice="INV-FEB-PARTIAL",
        total_tagihan=Decimal("80000.00"),
        status=InvoiceStatusEnum.partial,
        created_at=feb_date_1,
    )
    db_session.add(inv_feb_partial)
    await db_session.flush()

    pay_dp = Payment(
        id=801,
        invoice_id=inv_feb_partial.id,
        jumlah_bayar=Decimal("30000.00"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.dp,
        created_at=feb_date_1,
        settled_at=feb_date_1,
    )
    db_session.add(pay_dp)

    # 3. Order #3 (Created in February, CANCELLED with Non-Refundable DP 20,000 paid): Total 50,000
    order_feb_cancelled = Order(
        id=603,
        customer_id=customer.id,
        status=OrderStatusEnum.cancelled,
        metode_pengiriman=MetodePengirimanEnum.pickup,
        total_harga_pesanan=Decimal("50000.00"),
        created_at=feb_date_2,
    )
    db_session.add(order_feb_cancelled)
    await db_session.flush()

    inv_feb_cancelled = Invoice(
        id=703,
        order_id=order_feb_cancelled.id,
        nomor_invoice="INV-FEB-CANCELLED-DP",
        total_tagihan=Decimal("50000.00"),
        status=InvoiceStatusEnum.unpaid,
        created_at=feb_date_2,
    )
    db_session.add(inv_feb_cancelled)
    await db_session.flush()

    pay_non_refundable_dp = Payment(
        id=802,
        invoice_id=inv_feb_cancelled.id,
        jumlah_bayar=Decimal("20000.00"),
        payment_method="bca_va",
        payment_status=PaymentStatusEnum.success,
        payment_type=PaymentTypeEnum.dp,
        created_at=feb_date_2,
        settled_at=feb_date_2,
    )
    db_session.add(pay_non_refundable_dp)

    # 4. Order #4 (Created in February, REFUNDED): Total 40,000, Payment status Refunded
    order_feb_refunded = Order(
        id=604,
        customer_id=customer.id,
        status=OrderStatusEnum.refunded,
        metode_pengiriman=MetodePengirimanEnum.delivery,
        total_harga_pesanan=Decimal("40000.00"),
        created_at=feb_date_3,
    )
    db_session.add(order_feb_refunded)
    await db_session.flush()

    inv_feb_refunded = Invoice(
        id=704,
        order_id=order_feb_refunded.id,
        nomor_invoice="INV-FEB-REFUNDED",
        total_tagihan=Decimal("40000.00"),
        status=InvoiceStatusEnum.refunded,
        created_at=feb_date_3,
    )
    db_session.add(inv_feb_refunded)
    await db_session.flush()

    pay_refunded = Payment(
        id=803,
        invoice_id=inv_feb_refunded.id,
        jumlah_bayar=Decimal("40000.00"),
        payment_method="qris",
        payment_status=PaymentStatusEnum.refunded,
        payment_type=PaymentTypeEnum.final,
        created_at=feb_date_3,
        settled_at=feb_date_3,
    )
    db_session.add(pay_refunded)

    # 5. Operating Expense in February: 10,000
    expense_feb = Expense(
        kategori="Operasional Dapur",
        jumlah=Decimal("10000.00"),
        recorded_by=1,
        tanggal=datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(expense_feb)

    await db_session.commit()

    # ── Generate February Report ─────────────────────────────────────────────
    feb_report = await report_service.get_financial_report(db_session, "2026-02-01", "2026-02-28")

    # 1. Cash received:
    # 30,000 (from order_feb_partial DP) + 20,000 (from order_feb_cancelled DP) = 50,000
    # (Refunded payment is NOT success, order_jan had no payment)
    assert feb_report.cash_received == Decimal("50000.00")

    # 2. Non-refundable DP income:
    # 20,000 from order_feb_cancelled (status cancelled, payment success, within Feb)
    assert feb_report.non_refundable_dp_income == Decimal("20000.00")
    assert feb_report.other_income == Decimal("20000.00")

    # 3. Revenue & Gross profit:
    # No fully settled (paid) orders in Feb -> 0.00
    assert feb_report.revenue == Decimal("0.00")
    assert feb_report.gross_profit == Decimal("0.00")

    # 4. Expenses: 10,000
    assert feb_report.expenses_total == Decimal("10000.00")

    # 5. Net profit: gross_profit (0) - expenses (10,000) + non_refundable_dp (20,000) = 10,000!
    assert feb_report.net_profit == Decimal("10000.00")

    # 6. Cumulative Outstanding payments as of 2026-02-28:
    # - Order #1 from January: 100,000 (unpaid) -> MUST BE INCLUDED!
    # - Order #2 from February: 80,000 - 30,000 = 50,000 (partial)
    # - Order #3 (cancelled) and Order #4 (refunded): EXCLUDED!
    # Total outstanding = 100,000 + 50,000 = 150,000
    assert feb_report.outstanding_payments == Decimal("150000.00")

