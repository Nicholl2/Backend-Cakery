from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from datetime import datetime, time
from typing import List

from app.core.database import get_db
from app.api.dependencies import require_service_key
from app.schemas.report import FinancialReportSummary, TopProductSummary
from app.models.payment import Payment, PaymentStatusEnum
from app.models.expense import Expense
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.product import Product

router = APIRouter(
    tags=["Reports"],
    responses={
        401: {"description": "Unauthorized - invalid service key"}
    }
)

@router.get("/summary", response_model=FinancialReportSummary)
async def get_report_summary(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_service_key)
) -> FinancialReportSummary:
    """
    Get financial metrics and summary (Chatbot/Service calls protected by X-Service-Key).
    """
    try:
        start_dt = datetime.combine(datetime.strptime(start_date, "%Y-%m-%d"), time.min)
        end_dt = datetime.combine(datetime.strptime(end_date, "%Y-%m-%d"), time.max)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format tanggal tidak valid. Gunakan format YYYY-MM-DD."
        )

    # 1. Revenue
    revenue_query = await db.execute(
        select(func.sum(Payment.jumlah_bayar))
        .where(
            Payment.payment_status == PaymentStatusEnum.success,
            Payment.created_at >= start_dt,
            Payment.created_at <= end_dt
        )
    )
    revenue = revenue_query.scalar() or Decimal("0.00")

    # 2. Expenses
    expense_query = await db.execute(
        select(func.sum(Expense.jumlah))
        .where(
            Expense.tanggal >= start_dt,
            Expense.tanggal <= end_dt
        )
    )
    expenses = expense_query.scalar() or Decimal("0.00")

    # 3. Order Count & Avg Order Value
    order_stats_query = await db.execute(
        select(
            func.count(Order.id),
            func.avg(Order.total_harga_pesanan)
        )
        .where(
            Order.status != OrderStatusEnum.cancelled,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
    )
    res = order_stats_query.first()
    order_count = res[0] if res and res[0] is not None else 0
    avg_order_value = Decimal(str(res[1])) if res and res[1] is not None else Decimal("0.00")

    # 4. Top Products
    top_products_query = await db.execute(
        select(
            Product.id,
            Product.nama_produk,
            func.sum(OrderItem.jumlah).label("qty"),
            func.sum(OrderItem.subtotal).label("revenue")
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status != OrderStatusEnum.cancelled,
            Order.created_at >= start_dt,
            Order.created_at <= end_dt
        )
        .group_by(Product.id, Product.nama_produk)
        .order_by(func.sum(OrderItem.jumlah).desc())
        .limit(5)
    )
    top_products_rows = top_products_query.all()
    
    top_products = [
        TopProductSummary(
            product_id=row.id,
            nama_produk=row.nama_produk,
            qty=int(row.qty or 0),
            revenue=Decimal(str(row.revenue or "0.00"))
        )
        for row in top_products_rows
    ]

    return FinancialReportSummary(
        revenue=revenue,
        expenses=expenses,
        order_count=order_count,
        avg_order_value=avg_order_value,
        top_products=top_products
    )
