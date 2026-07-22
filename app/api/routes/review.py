from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import get_current_buyer_id, security
from app.schemas.review import ReviewCreate, ReviewUpdate, ReviewOut
from app.services import review_service

router = APIRouter(tags=["Reviews"])


@router.post("/", response_model=ReviewOut, status_code=status.HTTP_201_CREATED,
             summary="Buat ulasan produk baru (khusus Buyer)")
async def create_review(
    data: ReviewCreate,
    buyer_id: int = Depends(get_current_buyer_id),
    db: AsyncSession = Depends(get_db)
):
    return await review_service.create_review(db, buyer_id, data)


@router.get("/product/{product_id}", response_model=list[ReviewOut],
            summary="List semua ulasan untuk suatu produk")
async def list_product_reviews(
    product_id: int,
    db: AsyncSession = Depends(get_db)
):
    return await review_service.get_reviews_by_product(db, product_id)


@router.get("/{review_id}", response_model=ReviewOut,
            summary="Lihat detail ulasan berdasarkan ID")
async def get_review(
    review_id: int,
    db: AsyncSession = Depends(get_db)
):
    return await review_service.get_review_or_404(db, review_id)


@router.put("/{review_id}", response_model=ReviewOut,
            summary="Edit ulasan sendiri (khusus Buyer pemilik ulasan)")
async def update_review(
    review_id: int,
    data: ReviewUpdate,
    buyer_id: int = Depends(get_current_buyer_id),
    db: AsyncSession = Depends(get_db)
):
    return await review_service.update_review(
        db=db,
        review_id=review_id,
        buyer_id=buyer_id,
        data=data,
        is_admin_or_owner=False
    )


@router.delete("/{review_id}", summary="Hapus ulasan (oleh pemilik atau Admin/Owner)")
async def delete_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    credentials = Depends(security)
):
    # Parse JWT to check role
    from app.core.security import decode_token
    payload = decode_token(credentials.credentials)
    
    role = payload.get("role")
    role_level = payload.get("role_level")
    
    is_admin_or_owner = False
    buyer_id = None
    
    if role == "buyer":
        buyer_id = int(payload.get("sub"))
    elif role_level is not None and int(role_level) <= 2:
        is_admin_or_owner = True
    else:
        # Require some authorization
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized access."
        )
        
    await review_service.delete_review(
        db=db,
        review_id=review_id,
        buyer_id=buyer_id,
        is_admin_or_owner=is_admin_or_owner
    )
    return {"deleted": True, "review_id": review_id}
