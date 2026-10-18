from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories import expense_repo
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseUpdate,
    ExpenseDetailResponse,
    ExpenseAuditResponse,
)
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from datetime import timezone


EDITABLE_FIELDS = (
    "kategori",
    "jumlah",
    "tanggal",
    "deskripsi",
    "penerima",
    "metode_pembayaran",
    "bukti_url",
)


def _audit_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _expense_detail(expense) -> ExpenseDetailResponse:
    return ExpenseDetailResponse(
        **expense.__dict__,
        recorded_by_username=expense.recorded_by_user.username if expense.recorded_by_user else "Unknown",
    )


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
        tanggal=expense_data.tanggal,
        deskripsi=expense_data.deskripsi,
        penerima=expense_data.penerima,
        metode_pembayaran=expense_data.metode_pembayaran,
        bukti_url=expense_data.bukti_url,
    )
    await db.flush()
    await expense_repo.add_audit_event(
        db,
        expense.id,
        "created",
        recorded_by,
        {
            field: _audit_value(getattr(expense, field))
            for field in (*EDITABLE_FIELDS, "recorded_by")
        },
    )
    await db.commit()

    expense_with_recorder = await expense_repo.get_expense_by_id(db, expense.id)

    return _expense_detail(expense_with_recorder)


async def update_expense(
    db: AsyncSession,
    expense_id: int,
    expense_data: ExpenseUpdate,
    updated_by: int,
) -> ExpenseDetailResponse:
    expense = await expense_repo.get_expense_for_update(db, expense_id)
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if expense.is_voided:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Expense yang dibatalkan tidak dapat diedit")

    changes = expense_data.model_dump(exclude_unset=True)
    audit_changes = {}
    for field, new_value in changes.items():
        old_value = getattr(expense, field)
        if old_value != new_value:
            audit_changes[field] = {"before": _audit_value(old_value), "after": _audit_value(new_value)}
            setattr(expense, field, new_value)
    if not audit_changes:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Tidak ada perubahan data")

    expense.updated_by = updated_by
    await db.flush()
    await expense_repo.add_audit_event(db, expense.id, "updated", updated_by, audit_changes)
    await db.commit()
    expense = await expense_repo.get_expense_by_id(db, expense_id)
    return _expense_detail(expense)


async def void_expense(db: AsyncSession, expense_id: int, reason: str, voided_by: int) -> ExpenseDetailResponse:
    expense = await expense_repo.get_expense_for_update(db, expense_id)
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if expense.is_voided:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Expense sudah dibatalkan")

    voided_at = datetime.now(timezone.utc)
    expense.is_voided = True
    expense.void_reason = reason
    expense.voided_at = voided_at
    expense.voided_by = voided_by
    await db.flush()
    await expense_repo.add_audit_event(
        db,
        expense.id,
        "voided",
        voided_by,
        {
            "is_voided": {"before": False, "after": True},
            "void_reason": {"before": None, "after": reason},
            "voided_at": {"before": None, "after": voided_at.isoformat()},
        },
    )
    await db.commit()
    expense = await expense_repo.get_expense_by_id(db, expense_id)
    return _expense_detail(expense)


async def get_expense_audit(db: AsyncSession, expense_id: int) -> list[ExpenseAuditResponse]:
    expense = await expense_repo.get_expense_by_id(db, expense_id)
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    events = await expense_repo.get_expense_audit(db, expense_id)
    return [ExpenseAuditResponse.model_validate(event) for event in events]


async def get_expense(db: AsyncSession, expense_id: int) -> ExpenseDetailResponse:
    """Get single expense"""
    expense = await expense_repo.get_expense_by_id(db, expense_id)

    if not expense:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Expense not found"
        )

    return _expense_detail(expense)


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

    return [_expense_detail(expense) for expense in expenses]


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
    
    by_category = await expense_repo.get_expenses_by_category(db, start_date, end_date)
    count = await expense_repo.count_expenses(db, start_date, end_date)

    return {
        "total_expenses": total,
        "by_category": by_category,
        "count": count,
        "period": {
            "start_date": start_date,
            "end_date": end_date
        }
    }
