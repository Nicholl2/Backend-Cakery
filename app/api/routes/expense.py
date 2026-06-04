from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.expense import ExpenseCreate, ExpenseUpdate, ExpenseDetailResponse
from app.services import expense_service
from app.api.dependencies import get_current_user_id, require_admin_or_owner
from typing import List, Optional
from datetime import datetime

router = APIRouter(
    prefix="/expenses",
    tags=["Expenses"],
    responses={
        401: {"description": "Unauthorized - missing or invalid token"},
        403: {"description": "Forbidden - only Admin/Owner can record expenses"},
        404: {"description": "Expense not found"}
    }
)


@router.post("", response_model=ExpenseDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    expense_data: ExpenseCreate,
    user_id: int = Depends(get_current_user_id),
    role_level: int = Depends(require_admin_or_owner),
    db: AsyncSession = Depends(get_db)
) -> ExpenseDetailResponse:
    """
    Record new operational expense (Admin/Owner only).
    
    Only users with role level 1 (Owner) or 2 (Admin) can record expenses.
    This impacts P&L calculations.
    
    - **kategori**: Expense category (e.g., 'electricity', 'salary', 'rent')
    - **jumlah**: Expense amount (must be positive decimal)
    - **tanggal**: Optional date (defaults to current date/time)
    """
    return await expense_service.create_expense(db, expense_data, recorded_by=user_id)


@router.get("", response_model=List[ExpenseDetailResponse], status_code=status.HTTP_200_OK)
async def list_expenses(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    kategori: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
) -> List[ExpenseDetailResponse]:
    """
    List expenses with filtering (Authenticated users only).
    
    - **skip**: Number of items to skip (pagination)
    - **limit**: Maximum items to return (max 1000)
    - **kategori**: Filter by expense category
    - **start_date**: Filter expenses from this date
    - **end_date**: Filter expenses until this date
    """
    return await expense_service.list_expenses(
        db,
        skip=skip,
        limit=limit,
        kategori=kategori,
        start_date=start_date,
        end_date=end_date
    )


@router.get("/{expense_id}", response_model=ExpenseDetailResponse, status_code=status.HTTP_200_OK)
async def get_expense(
    expense_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
) -> ExpenseDetailResponse:
    """
    Get single expense by ID (Authenticated users only).
    """
    return await expense_service.get_expense(db, expense_id)


@router.get("/summary/dashboard", status_code=status.HTTP_200_OK)
async def get_expenses_summary(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Get expenses summary for P&L dashboard (Authenticated users only).
    
    Returns total expenses, breakdown by category, and count.
    
    - **start_date**: Optional period start date
    - **end_date**: Optional period end date
    """
    return await expense_service.get_expenses_summary(db, start_date, end_date)