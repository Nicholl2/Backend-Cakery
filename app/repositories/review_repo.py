from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update as sa_update
from sqlalchemy.orm import selectinload
from app.models.review import Review
from app.models.product import Product
from app.schemas.review import ReviewCreate, ReviewUpdate
from typing import Optional


async def get_by_id(db: AsyncSession, review_id: int) -> Optional[Review]:
    """Get review by ID with product and customer relationships preloaded."""
    stmt = select(Review).where(Review.id == review_id).options(
        selectinload(Review.product),
        selectinload(Review.customer)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_by_product(db: AsyncSession, product_id: int) -> list[Review]:
    """Get all reviews for a product."""
    stmt = select(Review).where(Review.product_id == product_id).order_by(Review.created_at.desc()).options(
        selectinload(Review.product),
        selectinload(Review.customer)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_all(db: AsyncSession) -> list[Review]:
    """Get all reviews."""
    stmt = select(Review).order_by(Review.created_at.desc()).options(
        selectinload(Review.product),
        selectinload(Review.customer)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def create(db: AsyncSession, customer_id: int, data: ReviewCreate) -> Review:
    """Create a new review and update product aggregate rating/count."""
    review = Review(
        product_id=data.product_id,
        customer_id=customer_id,
        rating=data.rating,
        komentar=data.komentar
    )
    db.add(review)
    await db.flush()
    await db.commit()
    await db.refresh(review)
    
    # Recalculate product rating
    await recalculate_product_rating(db, data.product_id)
    
    # Re-query to preload relationships
    return await get_by_id(db, review.id)


async def update(db: AsyncSession, review: Review, data: ReviewUpdate) -> Review:
    """Update a review and update product aggregate rating/count."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(review, field, value)
    db.add(review)
    await db.commit()
    await db.refresh(review)
    
    # Recalculate product rating
    await recalculate_product_rating(db, review.product_id)
    
    # Re-query to preload relationships
    return await get_by_id(db, review.id)


async def delete(db: AsyncSession, review: Review) -> bool:
    """Delete a review and update product aggregate rating/count."""
    product_id = review.product_id
    await db.delete(review)
    await db.commit()
    
    # Recalculate product rating
    await recalculate_product_rating(db, product_id)
    return True


async def recalculate_product_rating(db: AsyncSession, product_id: int) -> None:
    """Helper to recalculate Product.rating and Product.review_count."""
    stmt = select(
        func.count(Review.id).label("count"),
        func.avg(Review.rating).label("avg")
    ).where(Review.product_id == product_id)
    result = await db.execute(stmt)
    row = result.first()
    
    count = 0
    avg_rating = 0.0
    if row:
        count = row.count or 0
        avg_rating = float(row.avg) if row.avg is not None else 0.0
        
    await db.execute(
        sa_update(Product)
        .where(Product.id == product_id)
        .values(rating=avg_rating, review_count=count)
    )
    await db.commit()
