from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.dependencies import require_service_key
from app.schemas.customer import CustomerUpsert, CustomerOut, TakeoverSet, TakeoverStatus
from app.services import customer_service

router = APIRouter(
    tags=["Customers (Chatbot)"],
    dependencies=[Depends(require_service_key)],
    responses={
        401: {"description": "Invalid or missing X-Service-Key header"},
        404: {"description": "Customer not found"},
    },
)


admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin Takeover"],
    dependencies=[Depends(require_service_key)],
    responses={
        401: {"description": "Invalid or missing X-Service-Key header"},
    },
)


@router.get("", response_model=CustomerOut)
async def get_customer(
    nomor_wa: str = Query(..., description="Nomor WhatsApp customer"),
    db: AsyncSession = Depends(get_db),
) -> CustomerOut:
    """
    Ambil data customer berdasarkan nomor WA.
    Digunakan chatbot untuk cek apakah customer sudah terdaftar.
    """
    return await customer_service.get_customer(db, nomor_wa)


@router.post("", response_model=CustomerOut, status_code=status.HTTP_200_OK)
async def upsert_customer(
    data: CustomerUpsert,
    db: AsyncSession = Depends(get_db),
) -> CustomerOut:
    """
    UPSERT customer — create jika baru, update nama/alamat jika nomor_wa sudah ada.
    Digunakan chatbot saat customer pertama kali berinteraksi atau update profil.
    """
    customer, created = await customer_service.upsert_customer(db, data)
    return customer


@router.post(
    "/{nomor_wa}/takeover",
    response_model=TakeoverStatus,
    summary="Set status human takeover (aktif/nonaktif + waktu kedaluwarsa)",
)
async def set_takeover(
    nomor_wa: str,
    data: TakeoverSet,
    db: AsyncSession = Depends(get_db),
) -> TakeoverStatus:
    """
    Aktifkan atau nonaktifkan mode human takeover untuk customer tertentu.

    - `active=true` → Admin/owner mengambil alih percakapan; chatbot berhenti merespons.
    - `active=false` → Kembalikan kontrol ke chatbot.
    - `expires_at` → Wajib diisi saat `active=true`. Saat waktu ini lewat, chatbot
      harus menganggap takeover sudah berakhir (`is_expired=true`).
    """
    return await customer_service.set_takeover(db, nomor_wa, data)


@router.get(
    "/{nomor_wa}/takeover",
    response_model=TakeoverStatus,
    summary="Cek status human takeover — dipanggil chatbot sebelum membalas pesan",
)
async def get_takeover_status(
    nomor_wa: str,
    db: AsyncSession = Depends(get_db),
) -> TakeoverStatus:
    """
    Kembalikan status takeover saat ini.
    Chatbot wajib cek endpoint ini sebelum membalas; skip respons jika
    `human_takeover_active=true` DAN `is_expired=false`.
    """
    return await customer_service.get_takeover_status(db, nomor_wa)


@admin_router.get("/takeover-handlers", summary="Ambil daftar nomor WA admin yang siap takeover")
async def get_admin_takeover_handlers(
    db: AsyncSession = Depends(get_db)
):
    """
    Ambil daftar nomor WhatsApp admin yang siap untuk takeover live-chat.
    """
    return await customer_service.get_takeover_handlers(db)