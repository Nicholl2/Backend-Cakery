"""Global rate-limit middleware that can see routes added via ``include_router``.

Why this exists
---------------
SlowAPI's bundled ``SlowAPIMiddleware`` resolves the endpoint with
``route.matches(scope)`` on ``app.routes`` and requires the matched object to
carry an ``endpoint`` attribute. Under FastAPI 0.162 / Starlette 1.3,
``include_router`` registers an ``_IncludedRouter`` wrapper that exposes neither
``path`` nor ``endpoint``; the lookup therefore returns ``None`` and every
request is treated as exempt. The consequence is silent: the global default
limit only covered routes declared directly on the app (``/health``,
``/openapi.json``) and none of the ``/auth``, ``/orders``, ``/payments``,
``/products`` routes.

This middleware resolves the endpoint from ``effective_route_contexts()`` when
available (the ``_IncludedRouter`` shape), falls back to ``routes`` for plain
routers, and otherwise behaves exactly like SlowAPI's own middleware: routes
that carry their own ``@limiter.limit`` decorator are left to the decorator so
quotas and headers are not counted or emitted twice.
"""

import logging
from typing import Iterable, Optional

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount

logger = logging.getLogger(__name__)

RouteTable = list[tuple[object, object]]


def _collect_routes(routes: Iterable) -> RouteTable:
    """Flatten the route tree into ``(path_regex, endpoint)`` pairs."""
    table: RouteTable = []
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            # FastAPI ``_IncludedRouter``: one wrapper, many real routes.
            try:
                table.extend((ctx.path_regex, ctx.endpoint) for ctx in contexts())
            except Exception:  # pragma: no cover - defensive, see fallback below
                logger.warning("Could not flatten included router for rate limiting")
            continue

        endpoint = getattr(route, "endpoint", None)
        path_regex = getattr(route, "path_regex", None)
        if endpoint is not None and path_regex is not None:
            table.append((path_regex, endpoint))
            continue

        if isinstance(route, Mount):
            # Static files and sub-applications keep the previous behaviour:
            # they are not counted by the global limiter.
            continue

        children = getattr(route, "routes", None)
        if children:
            table.extend(_collect_routes(children))

    return table


def resolve_endpoint(table: RouteTable, scope: dict) -> Optional[object]:
    """Return the endpoint that will handle ``scope``, or ``None``.

    Method is intentionally ignored: a request to the wrong method on a known
    path still costs the server work and should share the path's bucket.
    """
    path = scope.get("path") or ""
    for path_regex, endpoint in table:
        if path_regex.match(path):
            return endpoint
    return None


def _endpoint_name(endpoint: object) -> str:
    return f"{getattr(endpoint, '__module__', '')}.{getattr(endpoint, '__name__', '')}"


def _has_own_limit(limiter, endpoint: object) -> bool:
    """True when the endpoint enforces its own limit through a decorator."""
    name = _endpoint_name(endpoint)
    return (
        name in limiter._exempt_routes
        or name in limiter._route_limits
        or name in limiter._dynamic_route_limits
    )


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the limiter's default quota to every routed endpoint."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._route_table: Optional[RouteTable] = None

    def _table(self, routes: Iterable) -> RouteTable:
        if self._route_table is None:
            self._route_table = _collect_routes(routes)
        return self._route_table

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limiter = request.app.state.limiter
        if not limiter.enabled:
            return await call_next(request)

        endpoint = resolve_endpoint(self._table(request.app.routes), request.scope)
        if endpoint is None or _has_own_limit(limiter, endpoint):
            return await call_next(request)

        try:
            limiter._check_request_limit(request, endpoint, in_middleware=True)
        except RateLimitExceeded as exc:
            handler = request.app.exception_handlers.get(
                type(exc), _rate_limit_exceeded_handler
            )
            return handler(request, exc)

        response = await call_next(request)
        return limiter._inject_headers(response, request.state.view_rate_limit)
