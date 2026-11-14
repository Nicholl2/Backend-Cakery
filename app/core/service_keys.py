"""Internal service credentials, shared by authentication and rate limiting.

Both ``app/api/dependencies.py`` (authorization) and the rate limiter need to
answer the same question: *is this request carrying a valid internal service
credential, and which one?* Keeping the mapping in a single module means a new
service key cannot be added to one place and forgotten in the other.
"""

import hmac
from typing import Optional

# Setting name -> capability scopes granted by that secret. A secret must be
# unique across settings; duplicated values fail closed (see
# ``service_identity``) instead of silently combining capabilities.
SERVICE_SCOPE_KEYS: dict[str, frozenset[str]] = {
    "service_api_key": frozenset({"orders:create", "payments:create"}),
    "service_orders_read_key": frozenset({"orders:read"}),
    "service_orders_cancel_key": frozenset({"orders:cancel"}),
    "service_customers_key": frozenset({"customers:manage"}),
    "service_refund_key": frozenset({"refunds:request"}),
    "service_payment_status_key": frozenset({"payments:read"}),
    "service_financial_summary_key": frozenset({"reports:summary"}),
    "service_owner_numbers_key": frozenset({"owners:read"}),
    "service_settlement_key": frozenset({"settlements:check"}),
    "service_wa_verification_key": frozenset({"identity:verify"}),
}

# Credential carriers. The chatbot may send the service key as a header or as
# a query parameter, so both must resolve to the same identity.
SERVICE_KEY_HEADER = "x-service-key"
SERVICE_KEY_QUERY = "service_key"
INTERNAL_KEY_HEADER = "x-internal-key"

# Rate-limit buckets for authenticated services are prefixed so the limiter can
# tell them apart from client IPs, and so logs/headers stay readable.
SERVICE_BUCKET_PREFIX = "service:"
INTERNAL_SERVICE_LABEL = "chatbot_internal"


def _matches(candidate: str, configured: str) -> bool:
    """Constant-time comparison that tolerates non-ASCII input.

    ``hmac.compare_digest`` raises ``TypeError`` when given a non-ASCII ``str``.
    Credentials arrive from a request header, so compare bytes instead of
    letting a crafted value turn into a 500.
    """
    return hmac.compare_digest(
        candidate.encode("utf-8", "surrogatepass"),
        configured.encode("utf-8", "surrogatepass"),
    )


def service_identity(candidate: Optional[str]) -> Optional[tuple[frozenset[str], str]]:
    """Return ``(scopes, bucket_label)`` for a valid service key, else ``None``.

    An unknown key and an ambiguous key (two settings holding the same secret)
    both return ``None``, matching the fail-closed behaviour of authorization.
    """
    if not candidate:
        return None

    settings = _settings()
    matched: list[tuple[str, frozenset[str]]] = []
    for setting_name, scopes in SERVICE_SCOPE_KEYS.items():
        configured = getattr(settings, setting_name, "") or ""
        if configured and _matches(candidate, configured):
            matched.append((setting_name, scopes))

    if len(matched) != 1:
        return None

    setting_name, scopes = matched[0]
    return scopes, SERVICE_BUCKET_PREFIX + setting_name


def service_scopes_for_key(candidate: Optional[str]) -> set[str]:
    """Return the capability scopes granted by ``candidate`` (empty if invalid)."""
    identity = service_identity(candidate)
    return set(identity[0]) if identity else set()


def service_bucket_from_request(request) -> Optional[str]:
    """Bucket label for a request carrying a valid service credential.

    Checks ``X-Service-Key``, then the ``service_key`` query parameter, then
    ``X-Internal-Key`` (the narrower credential used by the WhatsApp identity
    verification flow). Invalid credentials deliberately fall back to the
    client-IP bucket, so a bogus key cannot buy a fresh quota.
    """
    candidate = request.headers.get(SERVICE_KEY_HEADER) or request.query_params.get(
        SERVICE_KEY_QUERY
    )
    identity = service_identity(candidate)
    if identity:
        return identity[1]

    internal = request.headers.get(INTERNAL_KEY_HEADER)
    if internal and internal_key_matches(internal):
        return SERVICE_BUCKET_PREFIX + INTERNAL_SERVICE_LABEL

    return None


def internal_key_matches(candidate: Optional[str]) -> bool:
    """Return True when ``candidate`` is the configured chatbot internal key."""
    configured = getattr(_settings(), "chatbot_internal_key", "") or ""
    return bool(candidate and configured) and _matches(candidate, configured)


def _settings():
    # Imported lazily to avoid an import cycle at module load time.
    from app.core.config import settings

    return settings
