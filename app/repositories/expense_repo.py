from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc, and_
from app.models.expense import Expense
from app.models.user import User
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import selectinload


async def create_expense(
    db: AsyncSession,
    kategori: str,
    jumlah: Decimal,
    recorded_by: int,
    tanggal: Optional[datetime] = None
) -> Expense:
    """Create new expense"""
    expense = Expense(
        kategori=kategori,
        jumlah=jumlah,
        recorded_by=recorded_by,
        tanggal=tanggal or datetime.now()
    )
    db.add(expense)
    await db.flush()
    return expense


async def get_expense_by_id(db: AsyncSession, expense_id: int) -> Optional[Expense]:
    """Get expense by ID with recorder info"""
    result = await db.execute(
        select(Expense)
        .where(Expense.id == expense_id)
        .options(selectinload(Expense.recorded_by_user))
    )
    return result.scalars().first()


async def get_all_expenses(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    kategori: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> List[Expense]:
    """Get expenses with optional filters"""
    query = select(Expense).options(selectinload(Expense.recorded_by_user))

    filters = []
    if kategori:
        filters.append(Expense.kategori == kategori)
    if start_date:
        filters.append(Expense.tanggal >= start_date)
    if end_date:
        filters.append(Expense.tanggal <= end_date)

    if filters:
        query = query.where(and_(*filters))

    query = query.order_by(desc(Expense.tanggal)).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def get_total_expenses(
    db: AsyncSession,
    kategori: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Decimal:
    """Get sum of expenses for P&L calculation"""
    query = select(Expense)

    filters = []
    if kategori:
        filters.append(Expense.kategori == kategori)
    if start_date:
        filters.append(Expense.tanggal >= start_date)
    if end_date:
        filters.append(Expense.tanggal <= end_date)

    if filters:
        query = query.where(and_(*filters))

    result = await db.execute(query)
    expenses = result.scalars().all()
    return sum(e.jumlah for e in expenses) if expenses else Decimal("0.00")


async def get_expenses_by_category(
    db: AsyncSession
) -> dict[str, Decimal]:
    """Get total expenses grouped by category (for P&L dashboard)"""
    result = await db.execute(select(Expense))
    expenses = result.scalars().all()

    category_totals = {}
    for expense in expenses:
        if expense.kategori not in category_totals:
            category_totals[expense.kategori] = Decimal("0.00")
        category_totals[expense.kategori] += expense.jumlah

    return category_totals


async def count_expenses(db: AsyncSession) -> int:
    """Count total expenses"""
    result = await db.execute(select(Expense))
    return len(result.scalars().all())