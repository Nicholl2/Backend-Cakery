import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


async def notify_chatbot_order_event(order_id: int, event: str) -> None:
    """
    Send async fire-and-forget HTTP POST notification to Chatbot internal webhook.
    Supported events: 'paid', 'refunded', 'ready'
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

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=headers)
            logger.info(
                f"[CHATBOT_WEBHOOK] Successfully notified chatbot: event='{event}', order_id={order_id}, url='{url}', status={resp.status_code}"
            )
    except Exception as e:
        logger.error(
            f"[CHATBOT_WEBHOOK_ERROR] Failed notifying chatbot for order {order_id} (event: '{event}'): {e}"
        )


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


