"""
Payment & Order State Machine — Validasi transisi status satu arah.

Mencegah backward transition (misal: Success → Pending) yang bisa terjadi
akibat stale webhook atau duplikasi callback dari payment gateway.
"""

from app.models.payment import PaymentStatusEnum
from app.models.order import OrderStatusEnum

# ── PAYMENT STATE MACHINE ────────────────────────────────────────────────────

# Transisi yang diizinkan: {from_state: {allowed_to_states}}
PAYMENT_TRANSITIONS: dict[PaymentStatusEnum, set[PaymentStatusEnum]] = {
    PaymentStatusEnum.pending: {PaymentStatusEnum.success, PaymentStatusEnum.failed},
    PaymentStatusEnum.success: {PaymentStatusEnum.refunded},
    PaymentStatusEnum.failed: set(),      # terminal state
    PaymentStatusEnum.refunded: set(),    # terminal state
}

# Status terminal — tidak boleh berubah kecuali ke refunded (dari success)
PAYMENT_TERMINAL_STATES: set[PaymentStatusEnum] = {
    PaymentStatusEnum.success,
    PaymentStatusEnum.failed,
    PaymentStatusEnum.refunded,
}


def is_valid_payment_transition(
    old_status: PaymentStatusEnum,
    new_status: PaymentStatusEnum,
) -> bool:
    """
    Validasi apakah transisi status payment diperbolehkan.
    
    Returns:
        True jika transisi valid, False jika tidak.
        Jika old_status == new_status, dianggap idempotent no-op (return False).
    """
    if old_status == new_status:
        return False  # Idempotent skip — status sudah sama
    allowed = PAYMENT_TRANSITIONS.get(old_status, set())
    return new_status in allowed


def is_payment_terminal(status: PaymentStatusEnum) -> bool:
    """Cek apakah payment sudah berada di terminal state."""
    return status in PAYMENT_TERMINAL_STATES


# ── ORDER STATE MACHINE ──────────────────────────────────────────────────────

ORDER_TRANSITIONS: dict[OrderStatusEnum, set[OrderStatusEnum]] = {
    OrderStatusEnum.pending: {
        OrderStatusEnum.in_process,
        OrderStatusEnum.cancelled,
        OrderStatusEnum.refunded,
    },
    OrderStatusEnum.in_process: {
        OrderStatusEnum.ready,
        OrderStatusEnum.cancelled,
        OrderStatusEnum.refunded,
    },
    OrderStatusEnum.ready: {
        OrderStatusEnum.delivered,
        OrderStatusEnum.picked_up,
        OrderStatusEnum.cancelled,
        OrderStatusEnum.refunded,
    },
    OrderStatusEnum.cancelled: {
        OrderStatusEnum.refunded,
    },
    OrderStatusEnum.delivered: set(),    # terminal state
    OrderStatusEnum.picked_up: set(),    # terminal state
    OrderStatusEnum.refunded: set(),     # terminal state
}

ORDER_TERMINAL_STATES: set[OrderStatusEnum] = {
    OrderStatusEnum.delivered,
    OrderStatusEnum.picked_up,
    OrderStatusEnum.refunded,
}


def is_valid_order_transition(
    old_status: OrderStatusEnum,
    new_status: OrderStatusEnum,
) -> bool:
    """
    Validasi apakah transisi status order diperbolehkan.
    
    Returns:
        True jika transisi valid, False jika tidak.
    """
    if old_status == new_status:
        return False
    allowed = ORDER_TRANSITIONS.get(old_status, set())
    return new_status in allowed


def is_order_terminal(status: OrderStatusEnum) -> bool:
    """Cek apakah order sudah berada di terminal state."""
    return status in ORDER_TERMINAL_STATES
