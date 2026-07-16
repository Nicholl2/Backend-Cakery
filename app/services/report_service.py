from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from datetime import datetime, time, timedelta
from typing import Optional
from app.repositories import report_repo
from app.schemas.report import FinancialReportDetail, AnalyticsReport

def parse_dates(start_date: Optional[str], end_date: Optional[str]):
    try:
        if not end_date:
            end_dt = datetime.combine(datetime.now().date(), time.max)
        else:
            end_dt = datetime.combine(datetime.strptime(end_date, "%Y-%m-%d"), time.max)

        if not start_date:
            start_dt = datetime.combine((end_dt - timedelta(days=30)).date(), time.min)
        else:
            start_dt = datetime.combine(datetime.strptime(start_date, "%Y-%m-%d"), time.min)
        
        return start_dt, end_dt
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format tanggal tidak valid. Gunakan format YYYY-MM-DD."
        )

async def get_financial_report(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> FinancialReportDetail:
    """
    Retrieve financial report statistics.
    """
    start_dt, end_dt = parse_dates(start_date, end_date)
    data = await report_repo.get_financial_report_data(db, start_dt, end_dt)
    return FinancialReportDetail(**data)

async def get_analytics_report(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> AnalyticsReport:
    """
    Retrieve sales and customer review analytics.
    """
    start_dt, end_dt = parse_dates(start_date, end_date)
    data = await report_repo.get_analytics_report_data(db, start_dt, end_dt)
    return AnalyticsReport(**data)
