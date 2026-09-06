from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.dependencies import require_service_key, require_admin_or_owner, get_current_buyer
from app.models.buyer import Buyer
from app.schemas.order import OrderCreate, BuyerOrderCreate, OrderOut, OrderStatusUpdate
from app.services import order_service

router = APIRouter(
    tags=["Orders"],
    responses={
        401: {"description": "Unauthorized"},
        409: {"description": "Conflict/Customer masih memiliki tagihan aktif"},
        422: {"description": "Unprocessable Entity"},
    },
)


# ── BUYER JWT ORDER ENDPOINTS ───────────────────────────────────────────────

@router.post("/buyer", response_model=OrderOut, status_code=status.HTTP_201_CREATED,
             summary="Buat order baru khusus Buyer (autentikasi JWT)")
async def create_order_for_buyer(
    data: BuyerOrderCreate,
    buyer: Buyer = Depends(get_current_buyer),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Membuat pesanan baru untuk Buyer yang sedang login.
    `customer_id` didapatkan otomatis dari nomor HP/identitas Buyer token JWT.
    """
    order = await order_service.create_buyer_order(db, buyer, data)
    return OrderOut.model_validate(order)


@router.get("/buyer", response_model=list[OrderOut],
            summary="List riwayat pesanan milik Buyer yang sedang login")
async def list_buyer_orders(
    buyer: Buyer = Depends(get_current_buyer),
    db: AsyncSession = Depends(get_db),
) -> list[OrderOut]:
    """
    Mengambil semua pesanan milik Buyer yang sedang login.
    """
    orders = await order_service.get_buyer_orders(db, buyer)
    return [OrderOut.model_validate(o) for o in orders]


@router.get("/buyer/{id}", response_model=OrderOut,
            summary="Detail pesanan milik Buyer yang sedang login")
async def get_buyer_order_detail(
    id: int,
    buyer: Buyer = Depends(get_current_buyer),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Mengambil detail spesifik pesanan milik Buyer yang sedang login.
    Mengembalikan 404 jika pesanan tidak ditemukan atau bukan milik Buyer ini.
    """
    order = await order_service.get_buyer_order_by_id(db, buyer, id)
    return OrderOut.model_validate(order)


# ── CHATBOT & ADMIN ORDER ENDPOINTS ─────────────────────────────────────────

@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_service_key)],
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


@router.get("/latest", response_model=OrderOut, dependencies=[Depends(require_service_key)],
            summary="Ambil order terbaru customer berdasarkan nomor WA")
async def get_latest_order(
    nomor_wa: str = Query(..., description="Nomor WhatsApp customer"),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await order_service.get_customer_latest_order(db, nomor_wa)
    return OrderOut.model_validate(order)


@router.post("/{order_id}/cancel", dependencies=[Depends(require_service_key)],
             summary="Batalkan order customer")
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await order_service.cancel_order_by_customer(db, order_id)


@router.patch("/{order_id}/status", response_model=OrderOut,
              dependencies=[Depends(require_admin_or_owner)],
              summary="Update status order oleh admin")
async def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Update status order oleh Admin/Owner. Nilai status: pending, in_process, ready, delivered, picked_up, cancelled.
    Jika status diubah menjadi 'ready', memicu push notification webhook ke Chatbot Service.
    """
    order = await order_service.update_order_status(db, order_id, data.status)
    return OrderOut.model_validate(order)