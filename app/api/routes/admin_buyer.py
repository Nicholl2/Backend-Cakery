from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.api.dependencies import require_admin_or_owner
from app.models.buyer import Buyer
from app.schemas.auth import BuyerProfileResponse

router = APIRouter(
    prefix="/admin/buyers",
    tags=["Admin - Buyer Management"],
    dependencies=[Depends(require_admin_or_owner)],
)

@router.get("", response_model=list[BuyerProfileResponse], summary="List all Buyers")
async def get_buyers(
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    result = await db.execute(select(Buyer).order_by(Buyer.created_at.desc()).offset(skip).limit(limit))
    buyers = result.scalars().all()
    return [BuyerProfileResponse.model_validate(b) for b in buyers]

@router.get("/{buyer_id}", response_model=BuyerProfileResponse, summary="Get detail of a specific Buyer")
async def get_buyer(
    buyer_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Buyer).where(Buyer.id == buyer_id))
    buyer = result.scalars().first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Buyer tidak ditemukan.")
    return BuyerProfileResponse.model_validate(buyer)

@router.delete("/{buyer_id}", summary="Delete a Buyer permanently")
async def delete_buyer(
    buyer_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Buyer).where(Buyer.id == buyer_id))
    buyer = result.scalars().first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Buyer tidak ditemukan.")
    
    await db.delete(buyer)
    await db.commit()
    return {"message": f"Buyer dengan ID {buyer_id} berhasil dihapus."}
