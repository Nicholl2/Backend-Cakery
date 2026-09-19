import logging
from fastapi import APIRouter, status
from app.core.cache import app_cache
from app.services.chatbot_notify import fetch_whatsapp_number

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/public",
    tags=["Public"]
)

CACHE_KEY_KONTAK_WA = "public_kontak_wa"
CACHE_TTL_SECONDS = 60


@router.get("/kontak-toko", status_code=status.HTTP_200_OK)
async def get_kontak_toko():
    """
    Public endpoint untuk mendapatkan nomor kontak WhatsApp toko.
    Akses: Public (tanpa autentikasi).
    Menggunakan in-memory cache ±60 detik.
    """
    cached_number = app_cache.get(CACHE_KEY_KONTAK_WA)
    if cached_number is not None:
        return {"whatsapp": cached_number}

    wa_number = await fetch_whatsapp_number()
    app_cache.set(CACHE_KEY_KONTAK_WA, wa_number, ttl=CACHE_TTL_SECONDS)
    return {"whatsapp": wa_number}
