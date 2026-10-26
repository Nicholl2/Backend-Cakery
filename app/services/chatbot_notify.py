import asyncio
import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


async def notify_chatbot_order_event(order_id: int, event: str) -> None:
    """
    Send an async HTTP POST notification to Chatbot with bounded retries.
    Supported events: 'created', 'paid', 'refunded', 'ready', 'processing',
    'delivered', 'picked_up', 'completed', 'cancelled', 'overdue'
    Target URL: {settings.chatbot_url}/webhook/internal/orders/{order_id}/{event}
    Header: X-Internal-Key: {settings.chatbot_internal_key}
    """
    if not settings.chatbot_url:
        logger.debug("[CHATBOT_WEBHOOK] chatbot_url is empty; skipping push.")
        return

    base_url = settings.chatbot_url.rstrip("/")
    url = f"{base_url}/webhook/internal/orders/{order_id}/{event}"
    headers = {}
    if settings.chatbot_internal_key:
        headers["X-Internal-Key"] = settings.chatbot_internal_key
    headers["Idempotency-Key"] = f"order:{order_id}:{event}"

    last_error = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, headers=headers)
            if 200 <= resp.status_code < 300:
                logger.info(
                    "[CHATBOT_WEBHOOK] Notified chatbot: event='%s', order_id=%s, status=%s",
                    event, order_id, resp.status_code,
                )
                return
            last_error = RuntimeError(f"Chatbot returned HTTP {resp.status_code}")
            # Client errors are not transient; retrying them cannot repair the
            # request and may amplify a bad payload or credentials issue.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break
        except Exception as exc:
            last_error = exc

        if attempt < 3:
            await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

    logger.error(
        "[CHATBOT_WEBHOOK_ERROR] Failed notifying chatbot for order %s (event '%s') after bounded retries: %s",
        order_id, event, last_error,
    )


async def notify_order_processing(order_id: int) -> None:
    """Kirim notifikasi pesanan diproses ke webhook Chatbot."""
    await notify_chatbot_order_event(order_id, "processing")


async def notify_order_completed(order_id: int) -> None:
    """Kirim notifikasi pesanan selesai ke webhook Chatbot."""
    await notify_chatbot_order_event(order_id, "completed")


async def notify_payment_status(order_id: int, status: str = "paid") -> None:
    """
    Kirim notifikasi status pembayaran berhasil ke webhook Chatbot.
    Harus dieksekusi SETELAH db.commit() selesai.
    """
    await notify_chatbot_order_event(order_id, status)


async def notify_refund_status(order_id: int) -> None:
    """
    Kirim notifikasi refund berhasil ke webhook Chatbot.
    Harus dieksekusi SETELAH db.commit() selesai.
    """
    await notify_chatbot_order_event(order_id, "refunded")


async def fetch_whatsapp_number() -> str:
    """
    Mengambil nomor WhatsApp aktif dari Chatbot via GET {chatbot_url}/status (timeout 5s).
    Jika respon 200 dan keadaan == 'tersambung' serta terdapat field 'nomor', kembalikan nomor tersebut.
    Jika chatbot offline, timeout, atau terjadi error, fallback ke settings.CHATBOT_WA_NUMBER.
    """
    fallback_num = settings.CHATBOT_WA_NUMBER or settings.chatbot_wa_number or "6287881273160"

    if not settings.chatbot_url:
        return fallback_num

    base_url = settings.chatbot_url.rstrip("/")
    url = f"{base_url}/status"
    headers = {}
    if settings.chatbot_internal_key:
        headers["X-Internal-Key"] = settings.chatbot_internal_key

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("keadaan") == "tersambung" and data.get("nomor"):
                    return str(data["nomor"]).strip()
                logger.warning(
                    f"[CHATBOT_STATUS] Chatbot is not connected or missing nomor: {data}. Falling back to default."
                )
            else:
                logger.warning(
                    f"[CHATBOT_STATUS] Unexpected status code {resp.status_code} from {url}. Falling back to default."
                )
    except Exception as e:
        logger.warning(f"[CHATBOT_STATUS_ERROR] Failed fetching WhatsApp number from chatbot: {e}. Falling back to {fallback_num}")

    return fallback_num


