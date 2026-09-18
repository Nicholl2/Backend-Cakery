import asyncio
import functools
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError, DBAPIError
from fastapi import HTTPException, status
from datetime import datetime, time, timedelta
from typing import Optional

try:
    import asyncpg
    DEADLOCK_EXCEPTIONS = (
        asyncpg.exceptions.DeadlockDetectedError,
        OperationalError,
        DBAPIError,
    )
except (ImportError, AttributeError):
    DEADLOCK_EXCEPTIONS = (
        OperationalError,
        DBAPIError,
    )

from app.repositories import report_repo
from app.schemas.report import FinancialReportDetail, FinancialReportResponse, AnalyticsReport, ReportSummary

logger = logging.getLogger(__name__)

def retry_on_deadlock(max_retries: int = 3, delay: float = 0.5):
    """
    Decorator untuk mengulang (retry) otomatis eksekusi fungsi async bila terjadi
    DeadlockDetectedError, OperationalError, atau lock contention database PostgreSQL (maks 3x, jeda 0.5s).
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except HTTPException:
                    raise
                except Exception as e:
                    last_exception = e
                    error_msg = str(e).lower()
                    is_deadlock = (
                        isinstance(e, DEADLOCK_EXCEPTIONS)
                        or "deadlock" in error_msg
                        or "deadlockdetectederror" in error_msg
                        or "could not obtain lock" in error_msg
                        or "lock" in error_msg
                    )
                    if is_deadlock and attempt < max_retries:
                        logger.warning(
                            f"[REPORT_DEADLOCK_RETRY] Deadlock / OperationalError terdeteksi pada {func.__name__} "
                            f"(percobaan {attempt}/{max_retries}). Mengulang kembali dalam {delay} detik... Error: {repr(e)}"
                        )
                        db = kwargs.get("db") or (args[0] if args else None)
                        if db and hasattr(db, "rollback"):
                            try:
                                await db.rollback()
                            except Exception:
                                pass
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"DETAIL ERROR in {func.__name__}: {repr(e)}", exc_info=True)
                    raise
            raise last_exception
        return wrapper
    return decorator

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
    except ValueError as e:
        logger.error(f"DETAIL ERROR: {repr(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format tanggal tidak valid. Gunakan format YYYY-MM-DD."
        )

@retry_on_deadlock(max_retries=3, delay=0.5)
async def get_financial_report(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> FinancialReportResponse:
    """
    Retrieve financial report statistics.
    """
    start_dt, end_dt = parse_dates(start_date, end_date)
    data = await report_repo.get_financial_report_data(db, start_dt, end_dt)
    return FinancialReportResponse(**data)

@retry_on_deadlock(max_retries=3, delay=0.5)
async def get_financial_summary_report(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> dict:
    """
    Retrieve financial summary report for chatbot.
    """
    from app.schemas.report import FinancialReportSummary
    start_dt, end_dt = parse_dates(start_date, end_date)
    data = await report_repo.get_financial_report_data(db, start_dt, end_dt)
    
    top_products = [
        {
            "product_id": p["product_id"],
            "nama_produk": p["nama_produk"],
            "qty": p["qty_sold"],
            "revenue": p["total_revenue"]
        }
        for p in data.get("product_profitability", [])
    ][:5]
    
    data["top_products"] = top_products
    return FinancialReportSummary(**data).model_dump()

@retry_on_deadlock(max_retries=3, delay=0.5)
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

@retry_on_deadlock(max_retries=3, delay=0.5)
async def get_dashboard_summary(
    db: AsyncSession,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> ReportSummary:
    """
    Retrieve dashboard summary statistics for internal users (Owner, Admin, Staff).
    """
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.combine(datetime.strptime(start_date, "%Y-%m-%d"), time.min)
        except ValueError as e:
            logger.error(f"DETAIL ERROR: {repr(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format tanggal tidak valid. Gunakan format YYYY-MM-DD."
            )
    if end_date:
        try:
            end_dt = datetime.combine(datetime.strptime(end_date, "%Y-%m-%d"), time.max)
        except ValueError as e:
            logger.error(f"DETAIL ERROR: {repr(e)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format tanggal tidak valid. Gunakan format YYYY-MM-DD."
            )
    data = await report_repo.get_dashboard_summary_data(db, start_dt, end_dt)
    return ReportSummary(**data)
