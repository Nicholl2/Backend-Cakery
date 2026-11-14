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
