import logging
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_current_user, require_admin_or_owner, require_owner
from app.core.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/whatsapp",
    tags=["Admin WhatsApp"]
)


def _get_chatbot_headers() -> dict[str, str]:
    headers = {}
    if settings.chatbot_internal_key:
        headers["X-Internal-Key"] = settings.chatbot_internal_key
    return headers


@router.get("/status", status_code=status.HTTP_200_OK, dependencies=[Depends(require_admin_or_owner)])
async def get_whatsapp_status():
    """
    Mengambil status koneksi WhatsApp chatbot.
    Otorisasi: Admin atau Owner (Role level 1 atau 2).
    Meneruskan respon status 200 apa adanya dari chatbot.
    """
    if not settings.chatbot_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp belum dikonfigurasi."
        )

    base_url = settings.chatbot_url.rstrip("/")
    url = f"{base_url}/status"
    headers = _get_chatbot_headers()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"[ADMIN_WA_STATUS] Chatbot returned {resp.status_code}: {resp.text}")
                return JSONResponse(
                    status_code=resp.status_code,
                    content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"detail": resp.text}
                )
    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.error(f"[ADMIN_WA_STATUS_ERROR] Connection error or timeout contacting chatbot: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp tidak dapat dihubungi atau timeout."
        )
    except Exception as e:
        logger.error(f"[ADMIN_WA_STATUS_ERROR] Unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terjadi kesalahan saat menghubungi layanan chatbot WhatsApp."
        )


@router.get("/qr", dependencies=[Depends(require_owner)])
async def get_whatsapp_qr():
    """
    Mengambil QR Code autentikasi WhatsApp chatbot.
    Otorisasi: Khusus Owner (Role level 1).
    Meneruskan file gambar PNG dengan header Cache-Control: no-store.
    """
    if not settings.chatbot_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp belum dikonfigurasi."
        )

    base_url = settings.chatbot_url.rstrip("/")
    url = f"{base_url}/qr"
    headers = _get_chatbot_headers()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type="image/png",
                    headers={"Cache-Control": "no-store"}
                )
            elif resp.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="QR code tidak ditemukan atau chatbot sudah terhubung."
                )
            else:
                logger.warning(f"[ADMIN_WA_QR] Chatbot returned {resp.status_code}: {resp.text}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Layanan chatbot WhatsApp mengembalikan respon tidak valid."
                )
    except HTTPException:
        raise
    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.error(f"[ADMIN_WA_QR_ERROR] Connection error or timeout fetching QR: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp tidak dapat dihubungi atau timeout."
        )
    except Exception as e:
        logger.error(f"[ADMIN_WA_QR_ERROR] Unexpected error fetching QR: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terjadi kesalahan saat menghubungi layanan chatbot WhatsApp."
        )


@router.post("/ganti-nomor", status_code=status.HTTP_200_OK, dependencies=[Depends(require_owner)])
async def ganti_nomor_whatsapp(
    current_user: User = Depends(get_current_user)
):
    """
    Meminta chatbot WhatsApp untuk mereset sesi dan berganti nomor.
    Otorisasi: Khusus Owner (Role level 1).
    Mencatat log audit sebelum meneruskan pemanggilan ke chatbot (timeout minimal 65 detik).
    """
    if not settings.chatbot_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp belum dikonfigurasi."
        )

    base_url = settings.chatbot_url.rstrip("/")
    headers = _get_chatbot_headers()

    # 1. Audit Log: Fetch previous status to capture old number
    old_number = "unknown"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            status_resp = await client.get(f"{base_url}/status", headers=headers)
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                old_number = str(status_data.get("nomor", "unknown"))
    except Exception as e:
        logger.warning(f"[ADMIN_WA_GANTI_NOMOR] Could not pre-fetch old WhatsApp number for audit: {e}")

    audit_time = datetime.now(timezone.utc).isoformat()
    logger.info(
        f"[AUDIT] WhatsApp ganti-nomor requested by user_id={current_user.id} "
        f"(username='{current_user.username}', role='{getattr(current_user.role, 'nama_role', 'Owner')}') "
        f"at {audit_time} - previous_number='{old_number}'"
    )

    # 2. Forward request to chatbot POST /ganti-nomor with >= 65s timeout
    try:
        async with httpx.AsyncClient(timeout=70.0) as client:
            resp = await client.post(f"{base_url}/ganti-nomor", headers=headers)
            if resp.status_code in (200, 201):
                return {"status": "ok", "nomor_lama": old_number}
            else:
                logger.error(
                    f"[ADMIN_WA_GANTI_NOMOR_ERROR] Chatbot returned {resp.status_code}: {resp.text}"
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Gagal mereset nomor pada layanan chatbot WhatsApp."
                )
    except HTTPException:
        raise
    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.error(f"[ADMIN_WA_GANTI_NOMOR_ERROR] Connection/timeout error contacting chatbot: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Layanan chatbot WhatsApp tidak dapat dihubungi atau timeout."
        )
    except Exception as e:
        logger.error(f"[ADMIN_WA_GANTI_NOMOR_ERROR] Unexpected error: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Terjadi kesalahan saat memproses ganti nomor WhatsApp."
        )
