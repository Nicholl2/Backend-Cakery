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
routers, and otherwise behaves exactly like SlowAPI's own middleware, with two
deliberate refinements:

* Routes with their own ``@limiter.limit`` decorator still get the default
  quota applied for **IP** buckets. The decorator refuses requests that fail an
  authentication dependency before it runs, so without this a flood of 401s on
  a decorated route would be uncounted.
* **Service** buckets skip the default quota for decorated routes, because the
  decorator is what defines the (larger) service quota. Applying the 60/min
  default there would silently cap it.

Headers are emitted once: from the decorator for decorated routes, from here
otherwise.
"""

import logging
from typing import Iterable, Optional

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount

from app.core.http_security import get_rate_limit_key
from app.core.service_keys import SERVICE_BUCKET_PREFIX

logger = logging.getLogger(__name__)

RouteTable = list[tuple[object, Optional[frozenset[str]], object]]


def _methods_of(route: object) -> Optional[frozenset[str]]:
    """HTTP methods handled by a route, or ``None`` when it handles them all."""
    methods = getattr(route, "methods", None)
    if not methods:
        return None
    return frozenset(str(method).upper() for method in methods)


def _collect_routes(routes: Iterable) -> RouteTable:
    """Flatten the route tree into ``(path_regex, methods, endpoint)`` triples."""
    table: RouteTable = []
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            # FastAPI ``_IncludedRouter``: one wrapper, many real routes.
            try:
                table.extend(
                    (ctx.path_regex, _methods_of(ctx), ctx.endpoint)
                    for ctx in contexts()
                )
            except Exception:  # pragma: no cover - defensive, see fallback below
                logger.warning("Could not flatten included router for rate limiting")
            continue

        endpoint = getattr(route, "endpoint", None)
        path_regex = getattr(route, "path_regex", None)
        if endpoint is not None and path_regex is not None:
            table.append((path_regex, _methods_of(route), endpoint))
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

    Several routes can share one path (``GET /orders`` and ``POST /orders``).
    Matching on the method first picks the handler that will actually run, so
    its own limits and service bucket are honoured; a method that no route
    accepts falls back to the first path match, so wrong-method requests still
    share the path's bucket. ``HEAD`` is served by ``GET`` routes.
    """
    path = scope.get("path") or ""
    method = str(scope.get("method") or "").upper()
    if method == "HEAD":
        method = "GET"

    fallback: Optional[object] = None
    for path_regex, methods, endpoint in table:
        if not path_regex.match(path):
            continue
        if methods is None or method in methods:
            return endpoint
        if fallback is None:
            fallback = endpoint
    return fallback


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
        if endpoint is None:
            return await call_next(request)

        owns_limit = _has_own_limit(limiter, endpoint)
        if owns_limit and get_rate_limit_key(request).startswith(SERVICE_BUCKET_PREFIX):
            # The decorator already defines the service quota for this route and
            # emits its own headers; applying the default here would cap it.
            return await call_next(request)

        try:
            limiter._check_request_limit(request, endpoint, in_middleware=True)
        except RateLimitExceeded as exc:
            handler = request.app.exception_handlers.get(
                type(exc), _rate_limit_exceeded_handler
            )
            return handler(request, exc)

        response = await call_next(request)
        if owns_limit:
            # Decorated routes already injected their own headers.
            return response
        return limiter._inject_headers(response, request.state.view_rate_limit)
