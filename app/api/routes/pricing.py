from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import require_internal_user
from app.services.pricing_service import calculate_hpp, apply_margin
from app.schemas.pricing import PricingResponse

router = APIRouter(tags=["Pricing"])


@router.get("/product/{product_id}",
            dependencies=[Depends(require_internal_user)],
            response_model=PricingResponse)
async def get_price(product_id: int, margin: float = 30, db: AsyncSession = Depends(get_db)):
    hpp, detail = await calculate_hpp(db, product_id)

    price = apply_margin(hpp, margin)

    return PricingResponse(
        hpp=float(hpp),
        margin_percent=margin,
        recommended_price=float(price),
        breakdown=detail
    )

