import pytest

from app.core.config import settings
from app.services.chatbot_notify import notify_chatbot_order_event


@pytest.mark.asyncio
async def test_chatbot_webhook_retries_server_error_and_checks_success(monkeypatch):
    responses = [503, 204]
    calls = 0
    observed_headers = []

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            observed_headers.append(kwargs["headers"])
            return FakeResponse(responses.pop(0))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.services.chatbot_notify.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.services.chatbot_notify.asyncio.sleep", no_wait)
    monkeypatch.setattr(settings, "chatbot_url", "http://chatbot.test")
    monkeypatch.setattr(settings, "chatbot_internal_key", "test-key")

    await notify_chatbot_order_event(12, "paid")

    assert calls == 2
    assert observed_headers[0]["Idempotency-Key"] == "order:12:paid"


@pytest.mark.asyncio
async def test_chatbot_webhook_does_not_retry_client_error(monkeypatch):
    calls = 0

    class FakeResponse:
        status_code = 401

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return FakeResponse()

    monkeypatch.setattr("app.services.chatbot_notify.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "chatbot_url", "http://chatbot.test")
    monkeypatch.setattr(settings, "chatbot_internal_key", "test-key")

    await notify_chatbot_order_event(12, "paid")

    assert calls == 1
