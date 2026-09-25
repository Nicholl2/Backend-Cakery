from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update as sa_update
from sqlalchemy.orm import selectinload
from app.models.review import Review, ReviewImage
from app.models.product import Product
from app.schemas.review import ReviewCreate, ReviewUpdate
from typing import Optional


async def get_by_id(db: AsyncSession, review_id: int) -> Optional[Review]:
    """Get review by ID with product, customer, order, and images relationships preloaded."""
    stmt = (
        select(Review)
        .where(Review.id == review_id)
        .options(
            selectinload(Review.product),
            selectinload(Review.customer),
            selectinload(Review.order),
            selectinload(Review.images),
        )
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_by_product(db: AsyncSession, product_id: int) -> list[Review]:
    """Get all reviews for a product with images preloaded."""
    stmt = (
        select(Review)
        .where(Review.product_id == product_id)
        .order_by(Review.created_at.desc())
        .options(
            selectinload(Review.product),
            selectinload(Review.customer),
            selectinload(Review.order),
            selectinload(Review.images),
        )
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_all(db: AsyncSession, limit: int = 100, offset: int = 0) -> list[Review]:
    """Get all reviews with pagination and images preloaded."""
    stmt = (
        select(Review)
        .order_by(Review.created_at.desc())
        .options(
            selectinload(Review.product),
            selectinload(Review.customer),
            selectinload(Review.order),
            selectinload(Review.images),
        )
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    return result.scalars().all()



async def get_latest(db: AsyncSession, limit: int = 6) -> list[Review]:
    """Get latest reviews globally with eager loading for product, customer, and images."""
    stmt = (
        select(Review)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(limit)
        .options(
            selectinload(Review.product),
            selectinload(Review.customer),
            selectinload(Review.order),
            selectinload(Review.images),
        )
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_by_order_and_product(db: AsyncSession, order_id: int, product_id: int) -> Optional[Review]:
    """Get review by order_id and product_id with images preloaded."""
    stmt = (
        select(Review)
        .where(
            Review.order_id == order_id,
            Review.product_id == product_id
        )
        .options(
            selectinload(Review.product),
            selectinload(Review.customer),
            selectinload(Review.order),
            selectinload(Review.images),
        )
        .execution_options(populate_existing=True)
    )
    result = await db.execute(stmt)
    return result.scalars().first()




async def create(db: AsyncSession, customer_id: int, data: ReviewCreate) -> Review:
    """Create a new review and update product aggregate rating/count."""
    review = Review(
        order_id=data.order_id,
        product_id=data.product_id,
        customer_id=customer_id,
        rating=data.rating,
        komentar=data.komentar or data.comment,
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
    update_data = data.model_dump(exclude_unset=True)
    if "comment" in update_data and "komentar" not in update_data:
        update_data["komentar"] = update_data["comment"]
    elif "komentar" in update_data and "comment" not in update_data:
        update_data["comment"] = update_data["komentar"]

    for field, value in update_data.items():
        if hasattr(review, field):
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


# ── REVIEW IMAGES OPERATIONS ─────────────────────────────────────────────────

async def add_review_images(
    db: AsyncSession,
    review_id: int,
    image_urls: list[str],
) -> list[ReviewImage]:
    """Add multiple image URLs to a review."""
    new_images: list[ReviewImage] = []
    for url in image_urls:
        img = ReviewImage(
            review_id=review_id,
            image_url=url,
        )
        db.add(img)
        new_images.append(img)
    await db.commit()
    for img in new_images:
        await db.refresh(img)
    return new_images


async def get_review_image(
    db: AsyncSession,
    review_id: int,
    image_id: int,
) -> Optional[ReviewImage]:
    """Get a review image by id and review_id."""
    stmt = select(ReviewImage).where(
        ReviewImage.id == image_id,
        ReviewImage.review_id == review_id,
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def delete_review_image(
    db: AsyncSession,
    review_id: int,
    image_id: int,
) -> Optional[str]:
    """Delete a review image and return its URL for Cloudinary cleanup."""
    stmt = select(ReviewImage).where(
        ReviewImage.id == image_id,
        ReviewImage.review_id == review_id,
    )
    result = await db.execute(stmt)
    img = result.scalars().first()
    if not img:
        return None
    deleted_url = img.image_url
    await db.delete(img)
    await db.commit()
    return deleted_url

