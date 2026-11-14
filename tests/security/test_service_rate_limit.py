"""Rate-limit bucketing for internal services and the 429 scope marker.

The chatbot calls the API from a single container address, so keying it by IP
would make every WhatsApp customer share one quota. These tests pin the
behaviour that replaces it: authenticated services get their own bucket and a
larger quota, forged credentials get none, and 429 responses carry a stable
``X-RateLimit-Scope`` marker instead of relying on the message body.
"""

import httpx
import pytest

from app.core.config import settings
from app.core.rate_limiter import limiter, service_aware
from app.core.service_keys import service_scopes_for_key
from app.main import app

SERVICE_QUOTA_PATH = "/openapi.json"


def _docs_headers() -> dict[str, str]:
    token = (settings.docs_access_token or "").strip()
    return {"X-Docs-Token": token} if token else {}


def _client(ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 1))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def live_limiter():
    limiter.enabled = True
    limiter.reset()
    try:
        yield limiter
    finally:
        limiter.reset()
        limiter.enabled = False


# ── SERVICE BUCKETS ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_service_key_gets_its_own_bucket(live_limiter):
    if not settings.service_api_key:
        pytest.skip("service_api_key is not configured")

    async with _client("203.0.113.70") as client:
        headers = {**_docs_headers(), "X-Service-Key": settings.service_api_key}
        for _ in range(60):
            assert (await client.get(SERVICE_QUOTA_PATH, headers=headers)).status_code == 200

        blocked = await client.get(SERVICE_QUOTA_PATH, headers=headers)
        assert blocked.status_code == 429
        assert blocked.headers["x-ratelimit-scope"] == "service"

        # The same source IP without a credential still has a full bucket, so
        # service traffic no longer consumes the quota of direct clients.
        anonymous = await client.get(SERVICE_QUOTA_PATH, headers=_docs_headers())
        assert anonymous.status_code == 200
        assert anonymous.headers["x-ratelimit-remaining"] == "59"


@pytest.mark.asyncio
async def test_forged_service_keys_cannot_buy_fresh_buckets(live_limiter):
    async with _client("203.0.113.71") as client:
        statuses = []
        for index in range(61):
            response = await client.get(
                SERVICE_QUOTA_PATH,
                headers={**_docs_headers(), "X-Service-Key": f"forged-{index}"},
            )
            statuses.append(response.status_code)

        assert statuses[:60] == [200] * 60
        assert statuses[60] == 429
        assert response.headers["x-ratelimit-scope"] == "ip"


@pytest.mark.asyncio
async def test_service_key_query_parameter_uses_the_same_bucket(live_limiter):
    if not settings.service_api_key:
        pytest.skip("service_api_key is not configured")

    transport = httpx.ASGITransport(app=app, client=("203.0.113.72", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(60):
            response = await client.get(
                SERVICE_QUOTA_PATH,
                params={"service_key": settings.service_api_key},
                headers=_docs_headers(),
            )
            assert response.status_code == 200

        blocked = await client.get(
            SERVICE_QUOTA_PATH,
            params={"service_key": settings.service_api_key},
            headers=_docs_headers(),
        )
        assert blocked.status_code == 429
        assert blocked.headers["x-ratelimit-scope"] == "service"


@pytest.mark.asyncio
async def test_429_keeps_slowapi_body_and_exposes_scope_to_browsers(live_limiter):
    async with _client("203.0.113.73") as client:
        for _ in range(60):
            assert (await client.get(SERVICE_QUOTA_PATH, headers=_docs_headers())).status_code == 200

        blocked = await client.get(
            SERVICE_QUOTA_PATH,
            headers={**_docs_headers(), "Origin": "https://toti-cakery.vercel.app"},
        )

    assert blocked.status_code == 429
    assert blocked.headers["x-ratelimit-scope"] == "ip"
    assert blocked.headers["access-control-allow-origin"] == "https://toti-cakery.vercel.app"
    assert "x-ratelimit-scope" in blocked.headers["access-control-expose-headers"].lower()
    # Format body is intentionally unchanged; clients should read the header.
    assert blocked.json() == {"error": "Rate limit exceeded: 60 per 1 minute"}


# ── SERVICE QUOTAS ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_order_read_quota_matches_payment_status_polling(live_limiter, monkeypatch):
    """The chatbot polls /orders/latest per pending order, like payment status."""
    from fastapi import HTTPException

    from app.api.routes import order as order_route

    monkeypatch.setattr(settings, "service_orders_read_key", "test-orders-read-key")

    async def fake_latest_order(db, nomor_wa):
        raise HTTPException(status_code=404, detail="Order tidak ditemukan")

    monkeypatch.setattr(
        order_route.order_service, "get_customer_latest_order", fake_latest_order
    )

    async with _client("203.0.113.74") as client:
        headers = {"X-Service-Key": "test-orders-read-key"}
        for _ in range(240):
            response = await client.get(
                "/orders/latest", params={"nomor_wa": "628123"}, headers=headers
            )
            assert response.status_code == 404

        blocked = await client.get(
            "/orders/latest", params={"nomor_wa": "628123"}, headers=headers
        )

    assert blocked.status_code == 429
    assert blocked.headers["x-ratelimit-scope"] == "service"


@pytest.mark.asyncio
async def test_decorated_route_still_counts_unauthenticated_ip_requests(live_limiter):
    """401s raised by a route dependency must not escape the default quota."""
    async with _client("203.0.113.75") as client:
        statuses = []
        for _ in range(61):
            response = await client.get("/orders/latest", params={"nomor_wa": "628123"})
            statuses.append(response.status_code)

    assert statuses[:60] == [401] * 60
    assert statuses[60] == 429
    assert response.headers["x-ratelimit-scope"] == "ip"


# ── BUSINESS 429 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_business_429_has_no_scope_header(live_limiter, monkeypatch):
    """X-RateLimit-Scope marks limiter refusals only, so clients can branch on it."""
    from fastapi import HTTPException

    from app.api.routes import auth as auth_route

    async def fake_authenticate(db, login_data):
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak percobaan login gagal. Akun dikunci sementara.",
        )

    monkeypatch.setattr(auth_route.auth_service, "authenticate_user", fake_authenticate)

    async with _client("203.0.113.76") as client:
        response = await client.post(
            "/auth/login", json={"username": "tester", "password": "secret123"}
        )

    assert response.status_code == 429
    assert "x-ratelimit-scope" not in response.headers
    assert response.json()["detail"].startswith("Terlalu banyak percobaan login")


@pytest.mark.asyncio
async def test_business_429_on_undecorated_route_has_no_scope_header(
    live_limiter, monkeypatch
):
    """The OTP confirm flow returns 429 without a limiter, and must stay distinguishable."""
    from fastapi import HTTPException

    from app.api.routes import auth as auth_route

    monkeypatch.setattr(settings, "chatbot_internal_key", "test-internal-key")

    async def fake_confirm(db, nonce, sender_phone):
        raise HTTPException(
            status_code=429,
            detail="Batas percobaan verifikasi tercapai (maksimal 3 kali).",
        )

    monkeypatch.setattr(
        auth_route.buyer_auth_service, "confirm_wa_verification", fake_confirm
    )

    async with _client("203.0.113.77") as client:
        response = await client.post(
            "/auth/verify/wa/confirm",
            json={"nonce": "abc", "sender_phone": "628123456789"},
            headers={"X-Internal-Key": "test-internal-key"},
        )

    assert response.status_code == 429
    assert "x-ratelimit-scope" not in response.headers
    # Undecorated routes still get the default-quota counters, which is exactly
    # why presence of X-RateLimit-* is not a reliable discriminator.
    assert response.headers.get("x-ratelimit-limit") == "60"


# ── QUOTA SELECTION ──────────────────────────────────────────────────────────

def test_service_aware_switches_quota_on_bucket_prefix():
    limit = service_aware("5/minute", "60/minute")
    assert limit("service:service_api_key") == "60/minute"
    assert limit("203.0.113.5") == "5/minute"
    assert limit("service:chatbot_internal") == "60/minute"


# ── CREDENTIAL PARSING ───────────────────────────────────────────────────────

def test_invalid_and_non_ascii_keys_are_rejected_without_crashing():
    # hmac.compare_digest raises TypeError for non-ASCII str; the shared helper
    # compares bytes so a crafted header cannot turn into a 500.
    assert service_scopes_for_key("ñandú") == set()
    assert service_scopes_for_key("") == set()
    assert service_scopes_for_key(None) == set()
    assert service_scopes_for_key("definitely-not-a-configured-key") == set()
