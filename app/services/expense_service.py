from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories import expense_repo
from app.schemas.expense import ExpenseCreate, ExpenseUpdate, ExpenseResponse, ExpenseDetailResponse
from typing import List, Optional
from datetime import datetime
from decimal import Decimal


async def create_expense(
    db: AsyncSession,
    expense_data: ExpenseCreate,
    recorded_by: int
) -> ExpenseDetailResponse:
    """Create new expense (RBAC: Owner/Admin only)"""
    expense = await expense_repo.create_expense(
        db,
        kategori=expense_data.kategori,
        jumlah=expense_data.jumlah,
        recorded_by=recorded_by,
        tanggal=expense_data.tanggal
    )
    await db.commit()

    expense_with_recorder = await expense_repo.get_expense_by_id(db, expense.id)

    return ExpenseDetailResponse(
        **expense_with_recorder.__dict__,
        recorded_by_username=expense_with_recorder.recorded_by_user.username if expense_with_recorder.recorded_by_user else "Unknown"
    )


async def get_expense(db: AsyncSession, expense_id: int) -> ExpenseDetailResponse:
    """Get single expense"""
    expense = await expense_repo.get_expense_by_id(db, expense_id)

    if not expense:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expense not found"
        )

    return ExpenseDetailResponse(
        **expense.__dict__,
        recorded_by_username=expense.recorded_by_user.username if expense.recorded_by_user else "Unknown"
    )


async def list_expenses(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    kategori: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> List[ExpenseDetailResponse]:
    """List expenses with filtering"""
    expenses = await expense_repo.get_all_expenses(
        db,
        skip=skip,
        limit=limit,
        kategori=kategori,
        start_date=start_date,
        end_date=end_date
    )

    return [
        ExpenseDetailResponse(
            **expense.__dict__,
            recorded_by_username=expense.recorded_by_user.username if expense.recorded_by_user else "Unknown"
        )
        for expense in expenses
    ]


async def get_expenses_summary(
    db: AsyncSession,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> dict:
    """Get expenses summary for P&L dashboard"""
    total = await expense_repo.get_total_expenses(
        db,
        start_date=start_date,
        end_date=end_date
    )
    
    by_category = await expense_repo.get_expenses_by_category(db)
    count = await expense_repo.count_expenses(db)

    return {
        "total_expenses": total,
        "by_category": by_category,
        "count": count,
        "period": {
            "start_date": start_date,
            "end_date": end_date
        }
    }
