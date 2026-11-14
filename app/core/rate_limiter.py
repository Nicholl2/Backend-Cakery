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
from app.core.service_keys import SERVICE_BUCKET_PREFIX

# ── LIMITER INSTANCE ─────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["60/minute"],         # Default global: 60 req/min per bucket
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
RATE_PAYMENT_STATUS = "60/minute"         # GET /payments/{order_id}/status

# ── QUOTA UNTUK INTERNAL SERVICE ─────────────────────────────────────────────
# Chatbot memanggil backend dari satu alamat container, jadi semua pelanggan
# WhatsApp berbagi satu bucket. Kuota per-IP yang ketat (5 pesanan/menit) akan
# menolak pelanggan yang tidak bersalah saat ramai, padahal pemanggilnya adalah
# layanan internal yang sudah terautentikasi dan tidak bisa dijangkau dari
# internet. Service mendapat bucket sendiri dan kuota yang lebih longgar, bukan
# pembebasan penuh: kuota tetap ada supaya kunci yang bocor atau loop tak
# terkendali tidak menjadi tak terbatas.

RATE_SERVICE_ORDER_CREATE = "60/minute"
RATE_SERVICE_PAYMENT_CREATE = "60/minute"
RATE_SERVICE_PAYMENT_STATUS = "240/minute"


def service_aware(default: str, service: str):
    """Route limit that keeps ``default`` for clients and ``service`` for services.

    SlowAPI calls the returned callable with the request's rate-limit key (see
    ``LimitGroup``), which is why the decision can be made per request without
    parsing the request a second time.
    """
    def _limit(key: str) -> str:
        return service if key.startswith(SERVICE_BUCKET_PREFIX) else default

    return _limit
