from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from datetime import datetime, time
from typing import List, Optional

from app.core.database import get_db
from app.api.dependencies import require_internal_user, require_owner
from app.schemas.report import (
    FinancialReportSummary,
    ReportSummary,
    TopProductSummary,
    FinancialReportDetail,
    FinancialReportResponse,
    AnalyticsReport,
)
from app.services import report_service
from app.models.payment import Payment, PaymentStatusEnum
from app.models.expense import Expense
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.product import Product

router = APIRouter(
    tags=["Reports"],
    responses={
        401: {"description": "Unauthorized - Missing or invalid Bearer JWT"},
        403: {"description": "Forbidden - Insufficient permissions"}
    }
)

@router.get("/summary", response_model=ReportSummary)
async def get_report_summary(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_internal_user)
) -> ReportSummary:
    """
    Get dashboard summary (total products, active products, revenue, total orders, recent orders).
    Protected by JWT authorization for roles OWNER, ADMIN, and STAFF.
    """
    return await report_service.get_dashboard_summary(db, start_date, end_date)


@router.get("/financial", response_model=FinancialReportResponse)
async def get_financial_report(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_owner)
) -> FinancialReportResponse:
    """
    Get internal financial report for Owner.
    """
    return await report_service.get_financial_report(db, start_date, end_date)


@router.get("/analytics", response_model=AnalyticsReport)
async def get_analytics_report(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    _ = Depends(require_owner)
) -> AnalyticsReport:
    """
    Get sales and product review analytics for Owner.
    """
    return await report_service.get_analytics_report(db, start_date, end_date)
