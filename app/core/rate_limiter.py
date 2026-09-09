"""
In-Memory Rate Limiter — Menggunakan SlowAPI (tanpa Redis).

Rate limiter berbasis in-memory storage yang di-reset saat server restart.
Cocok untuk single-instance deployment tanpa infrastruktur Redis tambahan.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# ── LIMITER INSTANCE ─────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],         # Default global: 60 req/min per IP
    storage_uri="memory://",              # In-memory, zero dependency
)

# ── RATE LIMIT CONSTANTS ─────────────────────────────────────────────────────
# Digunakan sebagai decorator @limiter.limit(RATE_xxx) pada route

RATE_ORDER_CREATE = "5/minute"            # POST /orders, /orders/buyer, /orders/custom
RATE_PAYMENT_CREATE = "5/minute"          # POST /payments
RATE_AUTH_LOGIN = "10/minute"             # POST /auth/login, /auth/buyer/*
RATE_AUTH_VERIFY = "6/minute"             # POST /auth/verify/*
RATE_WEBHOOK = "30/minute"               # POST /payments/notify (global, bukan per-IP)
