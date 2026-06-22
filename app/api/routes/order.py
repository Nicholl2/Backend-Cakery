from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.dependencies import require_service_key
from app.schemas.order import OrderCreate, OrderOut
from app.services import order_service

router = APIRouter(
    tags=["Orders"],
    dependencies=[Depends(require_service_key)],
    responses={
        401: {"description": "Invalid or missing X-Service-Key header"},
        409: {"description": "Customer masih memiliki tagihan aktif"},
        422: {"description": "Produk tidak ditemukan / tidak aktif / belum ada harga"},
    },
)


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED,
             summary="Buat order baru — dipanggil oleh chatbot")
async def create_order(
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await order_service.create_new_order(
        db,
        customer_id=data.customer_id,
        items=[item.model_dump() for item in data.items],
        metode_pengiriman=data.metode_pengiriman,
        created_via=data.created_via,
    )
    return OrderOut.model_validate(order)