from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.repositories import review_repo, buyer_repo, customer_repo, product_repo, order_repo
from app.schemas.review import ReviewCreate, ReviewUpdate
from app.models.review import Review
from app.models.order import OrderStatusEnum
from app.utils.phone import get_phone_variants


def _is_matching_phone(phone1: Optional[str], phone2: Optional[str]) -> bool:
    if not phone1 or not phone2:
        return False
    if phone1 == phone2:
        return True
    return phone2 in get_phone_variants(phone1)



async def get_or_create_customer_from_buyer(db: AsyncSession, buyer_id: int):
    """Retrieve or create Customer based on the Buyer's phone number."""
    buyer = await buyer_repo.get_buyer_by_id(db, buyer_id)
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Buyer account not found."
        )
    
    # Map buyer.phone to customer.nomor_wa
    customer = await customer_repo.get_by_nomor_wa(db, buyer.phone)
    if not customer:
        customer, _ = await customer_repo.upsert(
            db,
            nomor_wa=buyer.phone,
            nama=buyer.name,
            alamat=None
        )
        await db.commit()
        await db.refresh(customer)
    return customer


async def create_review(db: AsyncSession, buyer_id: int, data: ReviewCreate) -> Review:
    """Create a new review for a product with order completion and eligibility checks."""
    # 1. Verify product exists
    product = await product_repo.get_by_id(db, data.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produk tidak ditemukan."
        )

    # 2. Get or create customer mapping
    customer = await get_or_create_customer_from_buyer(db, buyer_id)

    # 3. Verify order exists and belongs to this customer
    order = await order_repo.get_order_by_id(db, data.order_id)
    if not order or order.customer_id != customer.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya pesanan yang sudah selesai yang dapat diulas."
        )

    # Check order status is completed
    completed_statuses = {
        OrderStatusEnum.completed,
        OrderStatusEnum.delivered,
        OrderStatusEnum.picked_up,
    }
    if order.status not in completed_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hanya pesanan yang sudah selesai yang dapat diulas."
        )

    # 4. Verify product exists in order_items
    order_product_ids = [item.product_id for item in order.order_items if item.product_id is not None]
    if data.product_id not in order_product_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Produk tidak terdapat dalam pesanan ini."
        )

    # 5. Check duplicate review for (order_id, product_id)
    existing_review = await review_repo.get_by_order_and_product(db, data.order_id, data.product_id)
    if existing_review:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Anda sudah memberikan ulasan untuk produk pada pesanan ini."
        )

    # 6. Create review
    return await review_repo.create(db, customer.id, data)



async def get_reviews_by_product(db: AsyncSession, product_id: int) -> list[Review]:
    """Get all reviews for a product."""
    product = await product_repo.get_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produk tidak ditemukan."
        )
    return await review_repo.get_by_product(db, product_id)


async def get_latest_reviews(db: AsyncSession, limit: int = 6) -> list[Review]:
    """Get latest reviews globally without requiring product_id."""
    return await review_repo.get_latest(db, limit)


async def get_review_or_404(db: AsyncSession, review_id: int) -> Review:
    """Get review or raise 404."""
    review = await review_repo.get_by_id(db, review_id)
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ulasan tidak ditemukan."
        )
    return review


async def update_review(
    db: AsyncSession,
    review_id: int,
    buyer_id: int,
    data: ReviewUpdate,
    is_admin_or_owner: bool = False
) -> Review:
    """Update a review (only the owner or an admin/owner can update)."""
    review = await get_review_or_404(db, review_id)
    
    # Check authorization
    if not is_admin_or_owner:
        buyer = await buyer_repo.get_buyer_by_id(db, buyer_id)
        if not buyer or not _is_matching_phone(review.customer.nomor_wa, buyer.phone):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak memiliki izin untuk mengubah ulasan ini."
            )
            
    return await review_repo.update(db, review, data)


async def delete_review(
    db: AsyncSession,
    review_id: int,
    buyer_id: int,
    is_admin_or_owner: bool = False
) -> bool:
    """Delete a review (only the owner or an admin/owner can delete)."""
    review = await get_review_or_404(db, review_id)
    
    # Check authorization
    if not is_admin_or_owner:
        buyer = await buyer_repo.get_buyer_by_id(db, buyer_id)
        if not buyer or not _is_matching_phone(review.customer.nomor_wa, buyer.phone):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak memiliki izin untuk menghapus ulasan ini."
            )
            
    return await review_repo.delete(db, review)
