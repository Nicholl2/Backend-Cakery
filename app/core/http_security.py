"""HTTP hardening helpers shared by the FastAPI application.

This module centralises three concerns that must behave consistently for every
response, including error responses:

1. ``SecurityHeadersMiddleware`` — adds browser hardening headers.
2. ``DocsProtectionMiddleware`` — keeps ``/docs``, ``/redoc`` and
   ``/openapi.json`` reachable for the team without leaving them public.
3. ``get_rate_limit_key`` — derives the rate-limit bucket from the real client
   address, with an explicit opt-in for a trusted proxy header.

The middlewares are implemented as pure ASGI wrappers (not
``BaseHTTPMiddleware``) so streaming responses and exception handlers keep
working unchanged.
"""

import logging
import secrets
from ipaddress import ip_address
from urllib.parse import parse_qsl, urlencode

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ── API DOCS ─────────────────────────────────────────────────────────────────
# Paths are compared after removing ``root_path`` so ``/docs`` and ``/api/docs``
# are both protected (FastAPI serves both when ``root_path="/api"``).
DOCS_PATHS = frozenset(
    {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
    }
)

DOCS_COOKIE_NAME = "toti_docs_access"
DOCS_COOKIE_MAX_AGE = 8 * 60 * 60  # 8 hours
DOCS_TOKEN_HEADER = "x-docs-token"

# CSP for JSON/API responses: nothing may be loaded or framed from an API
# response. Docs pages are exempted because Swagger UI / ReDoc load their own
# assets; for those pages we only block framing.
API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
DOCS_CONTENT_SECURITY_POLICY = "frame-ancestors 'none'"

PERMISSIONS_POLICY = "geolocation=(), microphone=(), camera=()"

HSTS_MAX_AGE = 31536000  # 1 year


def resolve_docs_config(config) -> tuple[bool, str]:
    """Return ``(docs_enabled, docs_token)`` for the current configuration.

    Production fails safe: if docs are enabled but no access token is
    configured, docs are disabled rather than exposed publicly.
    """
    enabled = bool(getattr(config, "docs_enabled", True))
    token = (getattr(config, "docs_access_token", "") or "").strip()

    if enabled and (getattr(config, "environment", "") or "").lower() == "production":
        if not token:
            logger.warning(
                "[CONFIG_WARNING] API docs are disabled in production because "
                "DOCS_ACCESS_TOKEN is not set. Set DOCS_ACCESS_TOKEN to expose "
                "/docs, /redoc and /openapi.json behind a token."
            )
            enabled = False
        elif len(token) < 16:
            logger.warning(
                "[CONFIG_WARNING] DOCS_ACCESS_TOKEN is shorter than 16 characters; "
                "use a long random value."
            )

    return enabled, token


def _route_path(scope: Scope) -> str:
    """Return the request path with ``root_path`` removed."""
    path = scope.get("path") or "/"
    root_path = scope.get("root_path") or ""
    if root_path and (path == root_path or path.startswith(root_path + "/")):
        path = path[len(root_path):] or "/"
    return path


def _token_matches(provided: str, expected: str) -> bool:
    """Constant-time comparison that tolerates non-ASCII input."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8", "ignore"),
        expected.encode("utf-8", "ignore"),
    )


class SecurityHeadersMiddleware:
    """Attach hardening headers to every HTTP response.

    Headers already set by an inner layer are preserved: this middleware only
    fills gaps, so a route can still override a value deliberately.
    """

    def __init__(self, app: ASGIApp, *, hsts_max_age: int = HSTS_MAX_AGE) -> None:
        self.app = app
        self.hsts_max_age = hsts_max_age

    def _headers_for_scope(self, scope: Scope) -> list[tuple[str, str]]:
        path = _route_path(scope)
        is_docs = path in DOCS_PATHS
        headers = [
            ("x-content-type-options", "nosniff"),
            ("x-frame-options", "DENY"),
            ("referrer-policy", "no-referrer"),
            ("permissions-policy", PERMISSIONS_POLICY),
            (
                "content-security-policy",
                DOCS_CONTENT_SECURITY_POLICY if is_docs else API_CONTENT_SECURITY_POLICY,
            ),
        ]
        if scope.get("scheme") == "https":
            headers.append(
                ("strict-transport-security", f"max-age={self.hsts_max_age}")
            )
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers_to_add = self._headers_for_scope(scope)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in headers_to_add:
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)


class DocsProtectionMiddleware:
    """Require a token before serving the API documentation.

    Accepted credentials:
    * ``X-Docs-Token`` header (scripted access / client generation).
    * ``?token=...`` query parameter, which stores an ``HttpOnly`` cookie and
      redirects to the clean URL (browser access, Swagger UI keeps working).
    * The ``toti_docs_access`` cookie itself.

    A cookie is used instead of HTTP Basic auth on purpose: Basic credentials
    are cached by the browser for the whole ``/api/`` protection space and
    would be sent on normal API calls, colliding with Bearer authentication.
    """

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self.token = token

    def _is_authorized(self, headers: Headers) -> bool:
        if _token_matches(headers.get(DOCS_TOKEN_HEADER, ""), self.token):
            return True
        cookie_header = headers.get("cookie", "")
        if not cookie_header:
            return False
        cookies = {}
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                cookies[name] = value
        return _token_matches(cookies.get(DOCS_COOKIE_NAME, ""), self.token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        if _route_path(scope) not in DOCS_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if self._is_authorized(headers):
            await self.app(scope, receive, send)
            return

        query = parse_qsl(
            (scope.get("query_string") or b"").decode("latin-1"),
            keep_blank_values=True,
        )
        provided = next((value for key, value in query if key == "token"), "")
        if _token_matches(provided, self.token):
            remaining_query = urlencode(
                [(key, value) for key, value in query if key != "token"]
            )
            location = scope.get("path") or "/"
            if remaining_query:
                location = f"{location}?{remaining_query}"

            cookie_path = scope.get("root_path") or "/"
            response = RedirectResponse(location, status_code=302)
            response.headers["cache-control"] = "no-store"
            response.set_cookie(
                DOCS_COOKIE_NAME,
                self.token,
                max_age=DOCS_COOKIE_MAX_AGE,
                path=cookie_path,
                httponly=True,
                samesite="lax",
                secure=scope.get("scheme") == "https",
            )
            await response(scope, receive, send)
            return

        response = JSONResponse(
            {"detail": "Docs access token required"},
            status_code=401,
        )
        response.headers["cache-control"] = "no-store"
        await response(scope, receive, send)


def get_rate_limit_key(request: Request) -> str:
    """Return the rate-limit bucket key for a request.

    By default the connecting peer address is used (Uvicorn rewrites it from
    ``X-Forwarded-For`` for trusted proxies). ``TRUSTED_CLIENT_IP_HEADER`` is an
    explicit opt-in for deployments where the edge proxy overwrites a single
    header with the real client IP (for example ``CF-Connecting-IP``).
    """
    header_name = (getattr(_settings(), "trusted_client_ip_header", "") or "").strip().lower()
    if header_name:
        values = request.headers.getlist(header_name)
        if len(values) == 1:
            try:
                return str(ip_address(values[0].strip()))
            except ValueError:
                pass
    client = request.client
    if client and client.host:
        return str(client.host)
    return "unknown"


def _settings():
    # Imported lazily to avoid an import cycle at module load time.
    from app.core.config import settings

    return settings
