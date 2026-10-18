from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func
from app.models.expense import Expense, ExpenseAudit
from app.models.user import User
from typing import Optional, List
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import selectinload


async def create_expense(
    db: AsyncSession,
    kategori: str,
    jumlah: Decimal,
    recorded_by: int,
    tanggal: Optional[datetime] = None,
    deskripsi: Optional[str] = None,
    penerima: Optional[str] = None,
    metode_pembayaran: Optional[str] = None,
    bukti_url: Optional[str] = None,
) -> Expense:
    """Create new expense"""
    expense = Expense(
        kategori=kategori,
        jumlah=jumlah,
        recorded_by=recorded_by,
        tanggal=tanggal or datetime.now(timezone.utc),
        deskripsi=deskripsi,
        penerima=penerima,
        metode_pembayaran=metode_pembayaran,
        bukti_url=bukti_url,
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


async def get_expense_for_update(db: AsyncSession, expense_id: int) -> Optional[Expense]:
    result = await db.execute(
        select(Expense)
        .where(Expense.id == expense_id)
        .with_for_update()
        .options(selectinload(Expense.recorded_by_user))
    )
    return result.scalars().first()


async def add_audit_event(
    db: AsyncSession,
    expense_id: int,
    action: str,
    actor_id: int,
    changes: dict,
) -> ExpenseAudit:
    event = ExpenseAudit(
        expense_id=expense_id,
        action=action,
        actor_id=actor_id,
        changes=changes,
    )
    db.add(event)
    await db.flush()
    return event


async def get_expense_audit(db: AsyncSession, expense_id: int) -> list[ExpenseAudit]:
    result = await db.execute(
        select(ExpenseAudit)
        .where(ExpenseAudit.expense_id == expense_id)
        .order_by(ExpenseAudit.created_at, ExpenseAudit.id)
    )
    return list(result.scalars().all())


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
    """Get sum of expenses for P&L calculation via database aggregation"""
    query = select(func.coalesce(func.sum(Expense.jumlah), Decimal("0.00"))).where(Expense.is_voided.is_(False))

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
    total = result.scalar()
    return Decimal(str(total)) if total is not None else Decimal("0.00")


async def get_expenses_by_category(
    db: AsyncSession,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> dict[str, Decimal]:
    """Get total expenses grouped by category via SQL GROUP BY"""
    stmt = (
        select(Expense.kategori, func.coalesce(func.sum(Expense.jumlah), Decimal("0.00")))
        .where(Expense.is_voided.is_(False))
        .group_by(Expense.kategori)
    )
    if start_date:
        stmt = stmt.where(Expense.tanggal >= start_date)
    if end_date:
        stmt = stmt.where(Expense.tanggal <= end_date)
    result = await db.execute(stmt)
    rows = result.all()
    return {row[0]: Decimal(str(row[1])) for row in rows}


async def count_expenses(
    db: AsyncSession,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> int:
    """Count total expenses via SQL COUNT"""
    stmt = select(func.count(Expense.id)).where(Expense.is_voided.is_(False))
    if start_date:
        stmt = stmt.where(Expense.tanggal >= start_date)
    if end_date:
        stmt = stmt.where(Expense.tanggal <= end_date)
    result = await db.execute(stmt)
    return result.scalar() or 0
