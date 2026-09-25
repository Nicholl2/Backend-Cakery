from fastapi import APIRouter, Depends, status, HTTPException, Query, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import get_current_buyer_id, security
from app.schemas.review import ReviewCreate, ReviewUpdate, ReviewOut
from app.services import review_service

router = APIRouter(tags=["Reviews"])


@router.post("/", response_model=ReviewOut, status_code=status.HTTP_201_CREATED,
             summary="Buat ulasan produk baru (khusus Buyer, mendukung upload multiple foto)")
async def create_review(
    request: Request,
    buyer_id: int = Depends(get_current_buyer_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Buat ulasan produk baru.
    Mendukung 2 format request:
    1. application/json: {"order_id": int, "product_id": int, "rating": int, "comment": str}
    2. multipart/form-data: form fields (order_id, product_id, rating, comment/komentar) + files 'images'
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        order_id = form.get("order_id")
        product_id = form.get("product_id")
        rating = form.get("rating")
        comment = form.get("comment") or form.get("komentar")

        if not order_id or not product_id or not rating:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Field 'order_id', 'product_id', dan 'rating' wajib diisi."
            )

        try:
            data = ReviewCreate(
                order_id=int(order_id),
                product_id=int(product_id),
                rating=int(rating),
                comment=str(comment) if comment else None,
                komentar=str(comment) if comment else None,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

        # Extract files from all potential image keys (images, images[], files, etc.)
        raw_files = []
        for key in ["images", "images[]", "files", "files[]"]:
            raw_files.extend(form.getlist(key))
        if not raw_files:
            # Check any item with filename
            raw_files = [v for k, v in form.multi_items() if hasattr(v, "filename") and getattr(v, "filename", None)]

        files = [f for f in raw_files if hasattr(f, "filename") and getattr(f, "filename", None)]
        return await review_service.create_review(db, buyer_id, data, files=files)

    else:
        try:
            body = await request.json()
            data = ReviewCreate.model_validate(body)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        return await review_service.create_review(db, buyer_id, data, files=None)


@router.get("/latest", response_model=list[ReviewOut],
            summary="List ulasan terbaru secara global (Landing page / Frontend)")
async def list_latest_reviews(
    limit: int = Query(default=6, ge=1, le=50, description="Jumlah ulasan terbaru"),
    db: AsyncSession = Depends(get_db)
):
    return await review_service.get_latest_reviews(db, limit)


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


@router.post("/{review_id}/images", response_model=ReviewOut,
             summary="Upload multiple foto ke ulasan yang sudah ada (khusus Buyer pemilik ulasan)")
async def upload_review_images(
    review_id: int,
    images: list[UploadFile] = File(..., description="Daftar foto ulasan (JPEG/PNG/WEBP, maks 5MB per file)"),
    buyer_id: int = Depends(get_current_buyer_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload foto tambahan ke ulasan produk yang telah dibuat sebelumnya.
    Hanya pemilik ulasan yang berhak menambahkan foto.
    """
    return await review_service.upload_review_images(
        db=db,
        review_id=review_id,
        buyer_id=buyer_id,
        files=images,
        is_admin_or_owner=False,
    )


@router.delete("/{review_id}/images/{image_id}",
               summary="Hapus satu foto dari ulasan (oleh pemilik ulasan atau Admin/Owner)")
async def delete_review_image(
    review_id: int,
    image_id: int,
    db: AsyncSession = Depends(get_db),
    credentials = Depends(security)
):
    """
    Hapus salah satu foto dari ulasan produk.
    Bisa dilakukan oleh Buyer pemilik ulasan atau Admin/Owner.
    """
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized access."
        )

    return await review_service.delete_review_image(
        db=db,
        review_id=review_id,
        image_id=image_id,
        buyer_id=buyer_id,
        is_admin_or_owner=is_admin_or_owner,
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

