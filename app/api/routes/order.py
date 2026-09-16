from fastapi import APIRouter, Depends, Query, Request, status, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from app.core.rate_limiter import limiter, RATE_ORDER_CREATE

from app.core.database import get_db
from app.api.dependencies import (
    require_service_key,
    require_internal_user,
    get_current_buyer,
    get_auth_identity_optional_service_or_jwt,
    AuthIdentity,
)
from app.models.buyer import Buyer
from app.models.order import OrderStatusEnum
from app.schemas.order import (
    OrderCreate,
    BuyerOrderCreate,
    CustomOrderCreate,
    OrderOut,
    OrderStatusUpdate,
    RefundRequest,
    RefundResponse,
)
from app.repositories import customer_repo, order_repo
from app.services import order_service
from app.utils.pdf_generator import generate_order_invoice_pdf

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
@limiter.limit(RATE_ORDER_CREATE)
async def create_order_for_buyer(
    request: Request,
    data: BuyerOrderCreate,
    buyer: Buyer = Depends(get_current_buyer),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Membuat pesanan baru untuk Buyer yang sedang login.
    `customer_id` didapatkan otomatis dari nomor HP/identitas Buyer token JWT.
    """
    try:
        order = await order_service.create_buyer_order(db, buyer, data)
        return OrderOut.model_validate(order)
    except HTTPException as he:
        if he.status_code >= 500:
            logger.error(f"Server error in create_order_for_buyer converted to 400: {he.detail}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(he.detail) if he.detail else "Terjadi kesalahan data saat memproses pesanan.",
            )
        raise
    except Exception as e:
        logger.error(f"Unhandled exception in create_order_for_buyer: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


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


# ── CHATBOT & ADMIN/SELLER ORDER ENDPOINTS ──────────────────────────────────

@router.get("", response_model=list[OrderOut],
            dependencies=[Depends(require_internal_user)],
            summary="List seluruh pesanan toko (Khusus Staff/Admin/Owner)")
async def list_seller_orders(
    status: Optional[OrderStatusEnum] = Query(None, description="Filter status pesanan"),
    limit: int = Query(100, ge=1, le=500, description="Jumlah data per halaman"),
    offset: int = Query(0, ge=0, description="Offset pagination"),
    db: AsyncSession = Depends(get_db),
) -> list[OrderOut]:
    """
    Mengambil daftar seluruh pesanan untuk Seller (Staff/Admin/Owner) dengan relasi lengkap (Customer, OrderItems, Invoice, Payments).
    """
    orders = await order_service.get_seller_orders(
        db,
        limit=limit,
        offset=offset,
        status=status.value if status else None,
    )
    return [OrderOut.model_validate(o) for o in orders]


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_service_key)],
             summary="Buat order baru — dipanggil oleh chatbot")
@limiter.limit(RATE_ORDER_CREATE)
async def create_order(
    request: Request,
    data: OrderCreate,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await order_service.create_new_order(
        db,
        customer_id=data.customer_id,
        items=[item.model_dump() for item in data.items],
        metode_pengiriman=data.metode_pengiriman,
        created_via=data.created_via,
        notes=data.notes,
        due_date=data.due_date,
        payment_method_preference=data.payment_method_preference,
    )
    return OrderOut.model_validate(order)


@router.post("/custom", response_model=OrderOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_internal_user)],
             summary="Buat custom order tanpa master produk (Khusus Staff/Admin/Owner)")
@limiter.limit(RATE_ORDER_CREATE)
async def create_custom_order(
    request: Request,
    data: CustomOrderCreate,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Membuat pesanan custom baru tanpa master produk:
    - Membuat/mengambil record Customer secara otomatis berdasarkan customer_phone & customer_name.
    - Mengisi custom_product_name, price, dan qty tanpa master product_id.
    - Bypassing pemotongan stok bahan baku/resep.
    - Membuat Invoice otomatis dengan nomor unik.
    - Menetapkan created_via = 'seller'.
    """
    order = await order_service.create_custom_order(db, data)
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


@router.get("/{order_id}", response_model=OrderOut,
            dependencies=[Depends(require_internal_user)],
            summary="Detail pesanan berdasarkan ID (Khusus Staff/Admin/Owner)")
async def get_seller_order_detail(
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Mengambil detail pesanan spesifik untuk Seller (Staff/Admin/Owner) dengan relasi lengkap.
    """
    order = await order_service.get_seller_order_by_id(db, order_id)
    return OrderOut.model_validate(order)


@router.patch("/{order_id}/status", response_model=OrderOut,
              dependencies=[Depends(require_internal_user)],
              summary="Update status order oleh seller")
async def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    """
    Update status order oleh Seller (Staff/Admin/Owner). Nilai status: pending, in_process, ready, delivered, picked_up, cancelled, refunded.
    Jika status diubah menjadi 'ready', memicu push notification webhook ke Chatbot Service.
    Jika status diubah menjadi 'refunded', memicu push notification webhook /refunded ke Chatbot Service.
    Jika status diubah menjadi 'cancelled', stok bahan baku pesanan biasa akan dikembalikan secara otomatis.
    """
    order = await order_service.update_order_status(db, order_id, data.status.value)
    return OrderOut.model_validate(order)


@router.post("/{order_id}/refund", response_model=RefundResponse,
             summary="Proses refund untuk order yang sudah dibayar (Staff/Admin/Owner atau Chatbot Service Key)")
async def refund_order(
    order_id: int,
    data: RefundRequest,
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
) -> RefundResponse:
    """
    Melakukan proses refund untuk tagihan (Invoice) yang telah dibayar sebagian (DP) atau lunas.
    Dapat dipanggil oleh:
    1. Chatbot Service (via header X-Service-Key):
       - Memerlukan field nomor_wa pada request body.
       - Memvalidasi kepemilikan nomor telepon (403 jika tidak cocok).
       - Hanya diizinkan jika status pesanan masih 'pending' (400 jika sudah in_process atau seterusnya).
    2. Internal Seller (Staff/Admin/Owner via Bearer JWT):
       - Fleksibel sesuai kebijakan toko.

    Respons mengembalikan penanda refund_mode ('auto' jika API direct refund Midtrans berhasil,
    atau 'manual' jika pembayaran via VA/QRIS 412 yang memerlukan refund manual).
    """
    if auth.is_service:
        is_service_call = True
    elif auth.auth_type == "user":
        role_level = int(auth.role) if auth.role is not None else 3
        if role_level > 3:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hanya staff, admin, atau owner yang dapat memproses refund secara manual."
            )
        is_service_call = False
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak untuk peran ini."
        )

    return await order_service.cancel_and_refund_order(
        db=db,
        order_id=order_id,
        reason=data.reason,
        is_service=is_service_call,
        customer_phone=data.nomor_wa,
    )


@router.get("/{id}/invoice/pdf",
            summary="Download PDF Invoice pesanan (Buyer pemilik atau Staff/Admin/Owner)")
async def download_order_invoice_pdf(
    id: int,
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt),
    db: AsyncSession = Depends(get_db),
):
    """
    Menghasilkan dan mengunduh file PDF invoice pesanan Toti Cakery:
    - Eager-loads data order, customer, items, product, invoice, dan payments.
    - Autentikasi:
      * Buyer pemilik pesanan (nomor WA customer pada order cocok dengan akun Buyer login).
      * Internal Staff / Admin / Owner (role level 1, 2, 3).
    - Menghasilkan 404 jika order tidak ditemukan atau jika Buyer mencoba mengakses order milik orang lain.
    """
    order = await order_repo.get_order_with_details(db, id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order tidak ditemukan",
        )

    # Validasi Otorisasi
    if auth.is_buyer:
        customer = await customer_repo.get_by_nomor_wa(db, auth.buyer.phone)
        if not customer or order.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order tidak ditemukan",
            )
    elif auth.auth_type == "user":
        role_level = int(auth.role) if auth.role is not None else 99
        if role_level > 3:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akses ditolak untuk peran ini.",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak.",
        )

    # Attach payment amounts & ensure item product names
    await order_service._attach_payment_amounts(db, order)

    pdf_buffer = generate_order_invoice_pdf(order)

    filename = f"Invoice-TotiCakery-{id}.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers=headers,
    )