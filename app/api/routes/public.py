import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.cache import app_cache
from app.core.rate_limiter import limiter
from app.schemas.search import PublicSearchResponse
from app.services.public_search_service import search_public_content
from app.services.chatbot_notify import fetch_whatsapp_number

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/public",
    tags=["Public"]
)

CACHE_KEY_KONTAK_WA = "public_kontak_wa"
CACHE_TTL_SECONDS = 60


@router.get("/search", response_model=PublicSearchResponse, status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")
async def search_public(
    request: Request,
    q: str = Query(..., min_length=1, max_length=80),
    db: AsyncSession = Depends(get_db),
) -> PublicSearchResponse:
    """Search public products, active FAQs, and store contact metadata."""
    if re.search(r"[\x00-\x1f\x7f<>]", q):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Kata pencarian harus 2–80 karakter dan tidak boleh berisi tag atau karakter kontrol.",
        )
    normalized_query = " ".join(q.split())
    if len(normalized_query) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Kata pencarian harus 2–80 karakter setelah spasi dirapikan.",
        )
    return await search_public_content(db, normalized_query)


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
