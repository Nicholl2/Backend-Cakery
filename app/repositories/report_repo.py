from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from datetime import datetime
from app.models.payment import Payment, PaymentStatusEnum
from app.models.expense import Expense
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.product import Product
from app.models.review import Review

async def get_financial_report_data(db: AsyncSession, start_dt: datetime, end_dt: datetime) -> dict:
    """
    Get financial data: total revenue, total expenses, and total HPP cost.
    """
    # 1. total_revenue: SUM(payments.jumlah_bayar) WHERE payment_status = 'Success'
    revenue_query = await db.execute(
        select(func.sum(Payment.jumlah_bayar))
        .where(
            Payment.payment_status == PaymentStatusEnum.success,
            Payment.created_at >= start_dt,
            Payment.created_at <= end_dt
        )
    )
    total_revenue = revenue_query.scalar() or Decimal("0.00")

    # 2. total_expenses: SUM(expenses.jumlah)
    expense_query = await db.execute(
        select(func.sum(Expense.jumlah))
        .where(
            Expense.tanggal >= start_dt,
            Expense.tanggal <= end_dt
        )
    )
    total_expenses = expense_query.scalar() or Decimal("0.00")

    # 3. total_hpp_cost: SUM(order_items.jumlah * order_items.hpp_snapshot) JOIN orders WHERE orders.status != 'cancelled'
    hpp_query = await db.execute(
        select(func.sum(OrderItem.jumlah * OrderItem.hpp_snapshot))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status != OrderStatusEnum.cancelled,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    total_hpp_cost = hpp_query.scalar() or Decimal("0.00")

    gross_profit = total_revenue - total_hpp_cost
    net_profit = gross_profit - total_expenses

    return {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "total_hpp_cost": total_hpp_cost,
        "gross_profit": gross_profit,
        "net_profit": net_profit
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
