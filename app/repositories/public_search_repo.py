from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_item import FaqItem
from app.models.product import Category, Product


def _contains_pattern(query: str) -> str:
    """Build a bound LIKE pattern where user-provided wildcards stay literal."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_products(
    db: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[Product]:
    pattern = _contains_pattern(query)
    searchable_fields = (
        Product.nama_produk,
        Product.deskripsi,
        Product.kategori,
        Category.name,
        Category.description,
    )
    statement = (
        select(Product)
        .outerjoin(Category, Product.category_id == Category.id)
        .where(
            Product.is_active.is_(True),
            or_(*(field.ilike(pattern, escape="\\") for field in searchable_fields)),
        )
        .order_by(Product.nama_produk.asc(), Product.id.asc())
        .limit(limit)
    )
    result = await db.execute(statement)
    return list(result.scalars().all())


async def search_active_faqs(
    db: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[FaqItem]:
    pattern = _contains_pattern(query)
    statement = (
        select(FaqItem)
        .where(
            FaqItem.is_active.is_(True),
            or_(
                FaqItem.pertanyaan.ilike(pattern, escape="\\"),
                FaqItem.jawaban.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(FaqItem.pertanyaan.asc(), FaqItem.id.asc())
        .limit(limit)
    )
    result = await db.execute(statement)
    return list(result.scalars().all())
