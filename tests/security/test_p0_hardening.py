"""P0 hardening regression tests: security headers, docs protection, rate limiting."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import settings
from app.core.http_security import (
    API_CONTENT_SECURITY_POLICY,
    DOCS_CONTENT_SECURITY_POLICY,
    DOCS_COOKIE_NAME,
    DocsProtectionMiddleware,
    SecurityHeadersMiddleware,
    get_rate_limit_key,
    resolve_docs_config,
)
from app.core.rate_limiter import limiter
from app.main import app


def _docs_headers() -> dict[str, str]:
    """Docs are token-protected when DOCS_ACCESS_TOKEN is configured."""
    token = (settings.docs_access_token or "").strip()
    return {"X-Docs-Token": token} if token else {}


async def _page(request):
    return PlainTextResponse("ok")


def _docs_app(token: str) -> DocsProtectionMiddleware:
    inner = Starlette(
        routes=[
            Route("/docs", _page),
            Route("/openapi.json", _page),
            Route("/health", _page),
        ]
    )
    return DocsProtectionMiddleware(inner, token=token)


def _headers_app() -> SecurityHeadersMiddleware:
    inner = Starlette(routes=[Route("/thing", _page)])
    return SecurityHeadersMiddleware(inner)


# ── SECURITY HEADERS ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_security_headers_present_on_api_response():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "permissions-policy" in response.headers
    assert response.headers["content-security-policy"] == API_CONTENT_SECURITY_POLICY
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_security_headers_are_present_on_docs_and_rate_limit_responses():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/docs", headers=_docs_headers())

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    # Swagger UI loads its own assets, so docs keep a narrower CSP.
    assert response.headers["content-security-policy"] == DOCS_CONTENT_SECURITY_POLICY


@pytest.mark.asyncio
async def test_hsts_only_over_https():
    transport = httpx.ASGITransport(app=_headers_app(), client=("198.51.100.7", 1))
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get("/thing")
    assert "strict-transport-security" in response.headers
    assert "max-age=31536000" in response.headers["strict-transport-security"]

    transport = httpx.ASGITransport(app=_headers_app(), client=("198.51.100.7", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/thing")
    assert "strict-transport-security" not in response.headers


# ── DOCS PROTECTION ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_docs_require_token_when_configured():
    transport = httpx.ASGITransport(app=_docs_app("s3cret-docs-token"))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/docs")).status_code == 401
        assert (await client.get("/openapi.json")).status_code == 401
        assert (await client.get("/docs?token=wrong")).status_code == 401
        # Non-docs routes stay untouched.
        assert (await client.get("/health")).status_code == 200

        header_response = await client.get("/docs", headers={"X-Docs-Token": "s3cret-docs-token"})
        assert header_response.status_code == 200


@pytest.mark.asyncio
async def test_docs_query_token_sets_httponly_cookie_and_redirects():
    transport = httpx.ASGITransport(app=_docs_app("s3cret-docs-token"))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/docs?token=s3cret-docs-token", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/docs"
        assert "no-store" in response.headers["cache-control"]

        set_cookie = response.headers["set-cookie"]
        assert DOCS_COOKIE_NAME in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "token=s3cret-docs-token" not in response.headers["location"]

        followed = await client.get(response.headers["location"])
        assert followed.status_code == 200


@pytest.mark.asyncio
async def test_docs_open_when_no_token_configured():
    transport = httpx.ASGITransport(app=_docs_app(""))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/docs")).status_code == 200


@pytest.mark.asyncio
async def test_docs_protection_handles_root_path():
    transport = httpx.ASGITransport(app=_docs_app("s3cret-docs-token"), root_path="/api")
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.get("/api/docs")).status_code == 401
        assert (await client.get("/docs")).status_code == 401

        response = await client.get(
            "/api/docs?token=s3cret-docs-token", follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/api/docs"
        assert "Path=/api" in response.headers["set-cookie"]


def test_docs_config_fails_safe_in_production():
    class Config:
        docs_enabled = True
        docs_access_token = ""
        environment = "production"

    enabled, token = resolve_docs_config(Config())
    assert enabled is False
    assert token == ""

    Config.docs_access_token = "a-long-random-docs-token"
    enabled, token = resolve_docs_config(Config())
    assert enabled is True
    assert token == "a-long-random-docs-token"

    Config.environment = "development"
    Config.docs_access_token = ""
    enabled, _ = resolve_docs_config(Config())
    assert enabled is True


# ── RATE LIMIT KEY ───────────────────────────────────────────────────────────

def _request(headers=None, client=("203.0.113.9", 1234)):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": raw_headers,
            "client": client,
        }
    )


def test_rate_limit_key_uses_client_address_by_default(monkeypatch):
    monkeypatch.setattr(settings, "trusted_client_ip_header", "")
    assert get_rate_limit_key(_request()) == "203.0.113.9"


def test_rate_limit_key_trusted_header_is_opt_in_and_validated(monkeypatch):
    monkeypatch.setattr(settings, "trusted_client_ip_header", "CF-Connecting-IP")
    assert get_rate_limit_key(_request({"cf-connecting-ip": "198.51.100.4"})) == "198.51.100.4"
    # Invalid or duplicated header values fall back to the peer address.
    assert get_rate_limit_key(_request({"cf-connecting-ip": "not-an-ip"})) == "203.0.113.9"
    assert (
        get_rate_limit_key(_request({"cf-connecting-ip": "198.51.100.4, 198.51.100.5"}))
        == "203.0.113.9"
    )


# ── GLOBAL RATE LIMITING ─────────────────────────────────────────────────────

def test_rate_limit_middleware_is_registered():
    from app.core.rate_limit_middleware import GlobalRateLimitMiddleware

    assert GlobalRateLimitMiddleware in [middleware.cls for middleware in app.user_middleware]
    assert SecurityHeadersMiddleware in [middleware.cls for middleware in app.user_middleware]


# ── DECORATED ROUTES AND HEADER INJECTION ────────────────────────────────────

def test_endpoint_resolution_prefers_the_request_method():
    """``/orders`` is both GET (list, undecorated) and POST (create, decorated)."""
    from app.core.rate_limit_middleware import (
        _collect_routes,
        _has_own_limit,
        resolve_endpoint,
    )

    table = _collect_routes(app.routes)

    post = resolve_endpoint(table, {"path": "/orders", "method": "POST"})
    get = resolve_endpoint(table, {"path": "/orders", "method": "GET"})

    assert post.__name__ == "create_order"
    assert get.__name__ == "list_seller_orders"
    assert _has_own_limit(limiter, post)
    assert not _has_own_limit(limiter, get)
    # Methods that no route accepts still share the path's bucket.
    assert resolve_endpoint(table, {"path": "/orders", "method": "TRACE"}) is not None


def test_every_rate_limited_endpoint_declares_response():
    """SlowAPI needs a ``response`` argument to attach its headers.

    With ``headers_enabled=True``, a handler decorated with ``@limiter.limit``
    that returns a model/dict and does not declare ``response: Response`` makes
    SlowAPI call ``_inject_headers`` with ``None`` *after* the handler already
    succeeded, so every successful request becomes a 500.
    """
    import inspect

    from app.core.rate_limit_middleware import _collect_routes, _has_own_limit

    decorated = [
        endpoint
        for _, _, endpoint in _collect_routes(app.routes)
        if _has_own_limit(limiter, endpoint)
    ]

    assert decorated, "expected rate-limited endpoints in the route table"
    missing = sorted(
        f"{endpoint.__module__}.{endpoint.__name__}"
        for endpoint in decorated
        if "response" not in inspect.signature(endpoint).parameters
    )
    assert missing == [], f"missing `response: Response` parameter: {missing}"


@pytest.mark.asyncio
async def test_decorated_route_returning_model_succeeds_with_headers(monkeypatch):
    """A decorated route must return its model, not a 500 raised in _inject_headers."""
    from app.api.routes import auth as auth_route
    from app.schemas.auth import Token

    async def fake_authenticate(db, login_data):
        return Token(access_token="token", user_id=1, role_level=1, username="owner")

    monkeypatch.setattr(auth_route.auth_service, "authenticate_user", fake_authenticate)

    limiter.enabled = True
    limiter.reset()
    try:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.211", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/login", json={"username": "owner", "password": "secret123"}
            )

        assert response.status_code == 200, response.text
        assert response.json()["access_token"] == "token"
        # Route-specific quota, not the 60/minute default.
        assert response.headers["x-ratelimit-limit"] == "10"
        assert "x-ratelimit-remaining" in response.headers
        # Injected once, by the decorator.
        raw = [h for h in response.headers.raw if h[0].lower() == b"x-ratelimit-limit"]
        assert len(raw) == 1
    finally:
        limiter.reset()
        limiter.enabled = False


@pytest.mark.asyncio
async def test_undecorated_route_is_rate_limited():
    limiter.enabled = True
    limiter.reset()
    try:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.210", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            statuses = []
            for _ in range(66):
                response = await client.get("/openapi.json", headers=_docs_headers())
                statuses.append(response.status_code)

        assert statuses[0] == 200
        assert 429 in statuses
        assert statuses[-1] == 429
        assert statuses.index(429) == 60

        transport = httpx.ASGITransport(app=app, client=("203.0.113.210", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            blocked = await client.get("/openapi.json", headers=_docs_headers())
        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers
        # Error responses must keep the hardening headers too.
        assert blocked.headers["x-content-type-options"] == "nosniff"
        assert blocked.headers["x-frame-options"] == "DENY"
    finally:
        limiter.reset()
        limiter.enabled = False
