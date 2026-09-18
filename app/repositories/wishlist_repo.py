from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.buyer import Wishlist
from app.models.product import Product
from typing import Optional

async def add_wishlist(db: AsyncSession, buyer_id: int, product_id: int) -> bool:
    # Check if already exists
    result = await db.execute(select(Wishlist).where(
        Wishlist.buyer_id == buyer_id,
        Wishlist.product_id == product_id
    ))
    existing = result.scalars().first()
    if existing:
        return True # Or false indicating it was already there
    
    wishlist = Wishlist(buyer_id=buyer_id, product_id=product_id)
    db.add(wishlist)
    await db.commit()
    return True

async def remove_wishlist(db: AsyncSession, buyer_id: int, product_id: int) -> bool:
    result = await db.execute(select(Wishlist).where(
        Wishlist.buyer_id == buyer_id,
        Wishlist.product_id == product_id
    ))
    existing = result.scalars().first()
    if existing:
        await db.delete(existing)
        await db.commit()
    return True

async def get_buyer_wishlist_products(db: AsyncSession, buyer_id: int) -> list[Product]:
    # Select products joined with wishlist
    result = await db.execute(
        select(Product)
        .join(Wishlist, Wishlist.product_id == Product.id)
        .where(Wishlist.buyer_id == buyer_id)
    )
    return result.scalars().all()
