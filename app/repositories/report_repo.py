from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from datetime import datetime
from app.models.payment import Payment, PaymentStatusEnum
from app.models.expense import Expense
from app.models.order import Order, OrderItem, OrderStatusEnum, Invoice, InvoiceStatusEnum
from app.models.product import Product
from app.models.review import Review
from app.models.purchasing import Supplier, Purchase

async def get_financial_report_data(db: AsyncSession, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Get financial data: total revenue, total expenses, total HPP cost,
    outstanding payments, full product profitability, and supplier spending.
    Uses consistent settlement date basis and completely excludes unpaid/pending orders.
    """
    # ── 1. Subquery for Paid Orders with Consistent Settlement Date Basis ───────
    paid_orders_subquery = (
        select(
            Invoice.order_id.label("order_id"),
            func.max(func.coalesce(Payment.settled_at, Payment.created_at)).label("settlement_date")
        )
        .join(Payment, Payment.invoice_id == Invoice.id)
        .join(Order, Order.id == Invoice.order_id)
        .where(
            Payment.payment_status == PaymentStatusEnum.success,
            Invoice.status == InvoiceStatusEnum.paid,
            Order.status.notin_([OrderStatusEnum.cancelled, OrderStatusEnum.refunded])
        )
        .group_by(Invoice.order_id)
        .subquery()
    )

    paid_order_ids_in_period = (
        select(paid_orders_subquery.c.order_id)
        .where(
            paid_orders_subquery.c.settlement_date >= start_dt,
            paid_orders_subquery.c.settlement_date <= end_dt
        )
    )

    # ── 2. Total Revenue: Successful payments for orders settled in period ──────
    revenue_query = await db.execute(
        select(func.sum(Payment.jumlah_bayar))
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Payment.payment_status == PaymentStatusEnum.success,
            Invoice.order_id.in_(paid_order_ids_in_period)
        )
    )
    total_revenue = revenue_query.scalar() or Decimal("0.00")

    # ── 3. Total Expenses: Operating expenses within the date range ────────────
    expense_query = await db.execute(
        select(func.sum(Expense.jumlah))
        .where(
            Expense.tanggal >= start_dt,
            Expense.tanggal <= end_dt
        )
    )
    total_expenses = expense_query.scalar() or Decimal("0.00")

    # ── 4. Total HPP Cost: HPP ONLY for orders settled in the period ────────────
    # Excludes unpaid, pending, cancelled, or refunded orders completely
    hpp_query = await db.execute(
        select(func.sum(OrderItem.jumlah * OrderItem.hpp_snapshot))
        .where(
            OrderItem.order_id.in_(paid_order_ids_in_period)
        )
    )
    total_hpp_cost = hpp_query.scalar() or Decimal("0.00")

    gross_profit = total_revenue - total_hpp_cost
    net_profit = gross_profit - total_expenses

    # ── 5. Outstanding Payments (Piutang / Pembayaran Pending) ──────────────────
    paid_amount_subquery = (
        select(
            Payment.invoice_id,
            func.sum(Payment.jumlah_bayar).label("paid_sum")
        )
        .where(Payment.payment_status == PaymentStatusEnum.success)
        .group_by(Payment.invoice_id)
        .subquery()
    )

    outstanding_query = await db.execute(
        select(
            func.sum(
                Invoice.total_tagihan - func.coalesce(paid_amount_subquery.c.paid_sum, Decimal("0.00"))
            )
        )
        .join(Order, Order.id == Invoice.order_id)
        .outerjoin(paid_amount_subquery, paid_amount_subquery.c.invoice_id == Invoice.id)
        .where(
            Invoice.status.in_([InvoiceStatusEnum.unpaid, InvoiceStatusEnum.partial]),
            Order.status.notin_([OrderStatusEnum.cancelled, OrderStatusEnum.refunded]),
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    outstanding_payments = outstanding_query.scalar() or Decimal("0.00")

    # ── 6. Full Product Profitability (Breakdown per Produk) ────────────────────
    product_profit_query = await db.execute(
        select(
            func.coalesce(OrderItem.product_id, 0).label("product_id"),
            func.coalesce(Product.nama_produk, OrderItem.custom_product_name, "Custom Item").label("nama_produk"),
            func.sum(OrderItem.jumlah).label("qty_sold"),
            func.sum(OrderItem.subtotal).label("total_revenue"),
            func.sum(OrderItem.jumlah * OrderItem.hpp_snapshot).label("total_hpp"),
        )
        .outerjoin(Product, Product.id == OrderItem.product_id)
        .where(
            OrderItem.order_id.in_(paid_order_ids_in_period)
        )
        .group_by(
            func.coalesce(OrderItem.product_id, 0),
            func.coalesce(Product.nama_produk, OrderItem.custom_product_name, "Custom Item")
        )
        .order_by(
            func.sum(OrderItem.subtotal - (OrderItem.jumlah * OrderItem.hpp_snapshot)).desc()
        )
    )
    product_rows = product_profit_query.all()

    full_product_profitability = []
    for row in product_rows:
        p_rev = Decimal(str(row.total_revenue or 0))
        p_hpp = Decimal(str(row.total_hpp or 0))
        p_gross = p_rev - p_hpp
        margin = round(float((p_gross / p_rev) * 100), 2) if p_rev > 0 else 0.0
        full_product_profitability.append({
            "product_id": int(row.product_id),
            "nama_produk": str(row.nama_produk),
            "qty_sold": int(row.qty_sold or 0),
            "total_revenue": p_rev,
            "total_hpp": p_hpp,
            "gross_profit": p_gross,
            "margin_percentage": margin,
        })

    # ── 7. Supplier Spending (Pengeluaran per Supplier) ─────────────────────────
    supplier_spending_query = await db.execute(
        select(
            Supplier.id.label("supplier_id"),
            Supplier.nama_supplier,
            func.sum(Purchase.total_harga).label("total_spending"),
            func.count(Purchase.id).label("purchase_count")
        )
        .join(Purchase, Purchase.supplier_id == Supplier.id)
        .where(
            func.coalesce(Purchase.tanggal_pemesanan, Purchase.created_at) >= start_dt,
            func.coalesce(Purchase.tanggal_pemesanan, Purchase.created_at) <= end_dt
        )
        .group_by(Supplier.id, Supplier.nama_supplier)
        .order_by(func.sum(Purchase.total_harga).desc())
    )
    supplier_rows = supplier_spending_query.all()

    supplier_spending = [
        {
            "supplier_id": int(row.supplier_id),
            "nama_supplier": str(row.nama_supplier),
            "total_spending": Decimal(str(row.total_spending or 0)),
            "purchase_count": int(row.purchase_count or 0),
        }
        for row in supplier_rows
    ]

    return {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "total_hpp_cost": total_hpp_cost,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "outstanding_payments": outstanding_payments,
        "full_product_profitability": full_product_profitability,
        "supplier_spending": supplier_spending,
    }

async def get_analytics_report_data(db: AsyncSession, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Get analytics data: total distinct customers, chatbot conversion rate, and top reviewed product.
    """
    # 1. total_customers: COUNT(DISTINCT orders.customer_id) WHERE status != 'cancelled'
    cust_query = await db.execute(
        select(func.count(func.distinct(Order.customer_id)))
        .where(
            Order.status != OrderStatusEnum.cancelled,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    total_customers = cust_query.scalar() or 0

    # 2. conversion_rate_via_chatbot: (COUNT(orders) WHERE created_via='chatbot' / TOTAL orders) * 100
    chatbot_orders_query = await db.execute(
        select(func.count(Order.id))
        .where(
            Order.created_at >= start_dt,
            Order.created_at <= end_dt,
            Order.created_via == "chatbot"
        )
    )
    chatbot_orders_count = chatbot_orders_query.scalar() or 0

    total_orders_query = await db.execute(
        select(func.count(Order.id))
        .where(
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    total_orders_count = total_orders_query.scalar() or 0

    if total_orders_count > 0:
        conversion_rate_via_chatbot = (chatbot_orders_count / total_orders_count) * 100
    else:
        conversion_rate_via_chatbot = 0.0

    # 3. most_reviewed_product: JOIN reviews -> products, GROUP BY product_id, COUNT(reviews.id) AS review_count, AVG(reviews.rating) AS avg_rating. ORDER BY review_count DESC LIMIT 1
    most_reviewed_query = await db.execute(
        select(
            Product.nama_produk,
            func.avg(Review.rating).label("avg_rating"),
            func.count(Review.id).label("review_count")
        )
        .join(Review, Review.product_id == Product.id)
        .where(
            Review.created_at >= start_dt,
            Review.created_at <= end_dt
        )
        .group_by(Product.id, Product.nama_produk)
        .order_by(func.count(Review.id).desc())
        .limit(1)
    )
    row = most_reviewed_query.first()
    
    most_reviewed_product = None
    if row:
        most_reviewed_product = {
            "nama_produk": row[0],
            "avg_rating": float(round(row[1], 2)) if row[1] is not None else 0.0,
            "review_count": row[2]
        }

    return {
        "total_customers": total_customers,
        "conversion_rate_via_chatbot": float(round(conversion_rate_via_chatbot, 2)),
        "most_reviewed_product": most_reviewed_product
    }
