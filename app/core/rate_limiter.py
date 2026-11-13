"""
In-Memory Rate Limiter — Menggunakan SlowAPI (tanpa Redis).

Rate limiter berbasis in-memory storage yang di-reset saat server restart.
Cocok untuk single-instance deployment tanpa infrastruktur Redis tambahan.

PENTING: `default_limits` hanya ditegakkan jika `SlowAPIMiddleware` terdaftar
di aplikasi (lihat `app/main.py`). Tanpa middleware tersebut, limit default
tidak berlaku dan hanya route ber-decorator yang dibatasi.
"""

from slowapi import Limiter

from app.core.http_security import get_rate_limit_key

# ── LIMITER INSTANCE ─────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["60/minute"],         # Default global: 60 req/min per IP
    storage_uri="memory://",              # In-memory, zero dependency
    headers_enabled=True,                 # X-RateLimit-* + Retry-After untuk client
)

# ── RATE LIMIT CONSTANTS ─────────────────────────────────────────────────────
# Digunakan sebagai decorator @limiter.limit(RATE_xxx) pada route

RATE_ORDER_CREATE = "5/minute"            # POST /orders, /orders/buyer, /orders/custom
RATE_PAYMENT_CREATE = "5/minute"          # POST /payments
RATE_ORDER_STATUS_UPDATE = "30/minute"    # PATCH /orders/{order_id}/status
RATE_AUTH_LOGIN = "10/minute"             # POST /auth/login, /auth/buyer/*
RATE_AUTH_VERIFY = "6/minute"             # POST /auth/verify/*
RATE_WEBHOOK = "30/minute"               # POST /payments/notify (global, bukan per-IP)
