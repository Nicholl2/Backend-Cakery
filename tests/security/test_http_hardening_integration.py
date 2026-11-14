"""End-to-end HTTP hardening checks against the real application.

Unlike the unit tests in ``test_p0_hardening.py`` these exercise the assembled
middleware stack: Host validation, rate-limit enforcement with a live limiter,
CORS on error responses, and a real production-mode import.
"""

import os
import subprocess
import sys
import textwrap

import httpx
import pytest

from app.core.config import DEFAULT_ALLOWED_HOSTS, settings
from app.core.rate_limiter import limiter
from app.main import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _docs_headers() -> dict[str, str]:
    """Docs are token-protected when DOCS_ACCESS_TOKEN is configured."""
    token = (settings.docs_access_token or "").strip()
    return {"X-Docs-Token": token} if token else {}


def _client(host: str, ip: str = "203.0.113.20") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 4321))
    return httpx.AsyncClient(transport=transport, base_url=f"http://{host}")


# ── HOST VALIDATION ──────────────────────────────────────────────────────────

def test_allowed_hosts_default_covers_every_environment():
    hosts = settings.ALLOWED_HOSTS
    assert "toticakery.web.id" in hosts
    assert "*.toticakery.web.id" in hosts
    assert "backend-cakery.vercel.app" in hosts
    assert "*.vercel.app" in hosts
    for local_host in ("localhost", "127.0.0.1", "test", "testserver"):
        assert local_host in hosts


def test_empty_allowed_hosts_falls_back_to_defaults():
    original = settings.allowed_hosts
    try:
        settings.allowed_hosts = ""
        hosts = settings.ALLOWED_HOSTS
        assert hosts == [host.strip() for host in DEFAULT_ALLOWED_HOSTS.split(",")]
    finally:
        settings.allowed_hosts = original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "testserver",
        "localhost",
        "127.0.0.1",
        "test",
        "toticakery.web.id",
        "www.toticakery.web.id",
        "api.toticakery.web.id",
        "backend-cakery.vercel.app",
        "backend-cakery-abc123-team.vercel.app",
    ],
)
async def test_known_hosts_are_accepted(host):
    async with _client(host) as client:
        response = await client.get("/this-route-does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["evil.example.com", "vercel.app.evil.example", "203.0.113.99"])
async def test_unknown_hosts_are_rejected_with_hardening_headers(host):
    async with _client(host) as client:
        response = await client.get("/health")
    assert response.status_code == 400
    assert response.text == "Invalid host header"
    # The security-header middleware sits outside Host validation.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


# ── RATE LIMITING ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_response_is_browser_compatible():
    limiter.enabled = True
    limiter.reset()
    try:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.31", 1))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            for _ in range(60):
                assert (await client.get("/openapi.json", headers=_docs_headers())).status_code == 200

            blocked = await client.get(
                "/openapi.json",
                headers={"Origin": "https://toti-cakery.vercel.app", **_docs_headers()},
            )

            # A different client keeps its own bucket.
            other_transport = httpx.ASGITransport(app=app, client=("203.0.113.32", 1))
            async with httpx.AsyncClient(
                transport=other_transport, base_url="http://testserver"
            ) as other_client:
                other = await other_client.get("/openapi.json", headers=_docs_headers())

            # A different path on the same client also keeps its own bucket.
            same_client_other_path = await client.get("/docs", headers=_docs_headers())

        assert blocked.status_code == 429
        assert blocked.headers["access-control-allow-origin"] == "https://toti-cakery.vercel.app"
        assert int(blocked.headers["retry-after"]) > 0
        assert blocked.headers["x-ratelimit-limit"] == "60"
        assert blocked.headers["x-content-type-options"] == "nosniff"
        assert blocked.headers["x-frame-options"] == "DENY"

        assert other.status_code == 200
        assert same_client_other_path.status_code == 200
    finally:
        limiter.reset()
        limiter.enabled = False


# ── DEFAULT LIMIT COVERAGE ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_limit_covers_routes_inside_included_routers():
    """Routes registered with include_router must still get the default quota.

    Starlette 1.x hides included routes behind ``_IncludedRouter`` wrappers, which
    made SlowAPI's bundled middleware treat every one of them as exempt.
    """
    limiter.enabled = True
    limiter.reset()
    try:
        async with _client("testserver", ip="203.0.113.40") as client:
            statuses = []
            for _ in range(61):
                response = await client.get("/orders/latest")
                statuses.append(response.status_code)
    finally:
        limiter.reset()
        limiter.enabled = False

    # The route needs a service key, so it answers 401 - but each attempt is
    # still counted and the 61st is refused.
    assert statuses[:60] == [401] * 60
    assert statuses[60] == 429
    assert response.headers["x-ratelimit-scope"] == "ip"


# ── PRODUCTION MODE ──────────────────────────────────────────────────────────

PRODUCTION_SNIPPET = textwrap.dedent(
    """
    import asyncio

    import httpx

    from app.main import app


    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            for path in ("/docs", "/redoc", "/openapi.json", "/api/docs"):
                response = await client.get(path)
                assert response.status_code == 404, (path, response.status_code)

            response = await client.get("/does-not-exist")
            assert response.status_code == 404
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "content-security-policy" in response.headers
            assert (
                response.headers["content-security-policy"]
                == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )


    asyncio.run(main())
    print("production-checks-ok")
    """
)


def _run_production_import(extra_env: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "integration-test-production-secret-key",
            "SERVICE_API_KEY": "integration-test-service-key",
            "DATABASE_URL": "sqlite+aiosqlite://",
            "DOCS_ENABLED": "true",
            "DOCS_ACCESS_TOKEN": "",
        }
    )
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", PRODUCTION_SNIPPET],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_production_disables_docs_without_token():
    result = _run_production_import({})
    assert result.returncode == 0, result.stderr
    assert "production-checks-ok" in result.stdout
    assert "DOCS_ACCESS_TOKEN is not set" in result.stderr
