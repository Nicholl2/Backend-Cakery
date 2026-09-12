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
