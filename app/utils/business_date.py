"""
Centralized business date/time utility for Toti Cakery.

All order date validations use Asia/Jakarta timezone to determine the
current business date, avoiding bugs where UTC vs WIB date boundaries
differ (e.g. UTC 17:30 = WIB 00:30 next day).
"""

from datetime import date, datetime, timedelta, timezone

# Asia/Jakarta is UTC+7, fixed offset (Indonesia does not observe DST).
WIB = timezone(timedelta(hours=7))

# Business rules
MIN_DAYS_AHEAD = 1   # H+1: earliest fulfillment is tomorrow
MAX_DAYS_AHEAD = 30  # H+30: latest fulfillment is 30 calendar days from today


def get_business_today() -> date:
    """Return today's date in Asia/Jakarta (WIB) timezone."""
    return datetime.now(WIB).date()


def get_business_now() -> datetime:
    """Return current datetime in Asia/Jakarta (WIB) timezone."""
    return datetime.now(WIB)


def validate_fulfillment_date(fulfillment_dt: datetime) -> datetime:
    """
    Validate that a fulfillment date/datetime meets business rules:
    - Must be at least H+1 (tomorrow in WIB)
    - Must be at most H+30 (30 calendar days from today in WIB)

    Args:
        fulfillment_dt: The requested fulfillment datetime.

    Returns:
        The validated datetime (unchanged).

    Raises:
        ValueError: If the date violates business rules.
    """
    today = get_business_today()
    min_date = today + timedelta(days=MIN_DAYS_AHEAD)
    max_date = today + timedelta(days=MAX_DAYS_AHEAD)

    # Extract just the date portion for comparison
    if isinstance(fulfillment_dt, datetime):
        # If naive, assume UTC so serialized response preserves timezone info
        if fulfillment_dt.tzinfo is None:
            fulfillment_dt = fulfillment_dt.replace(tzinfo=timezone.utc)
        fulfillment_date = fulfillment_dt.astimezone(WIB).date()
    else:
        fulfillment_date = fulfillment_dt

    if fulfillment_date < min_date:
        raise ValueError(
            f"Pemesanan kue minimal H-1 sebelum tanggal pengambilan/pengiriman (minimal H+1, paling cepat {min_date.isoformat()}). "
            f"Tanggal yang diminta: {fulfillment_date.isoformat()}."
        )

    if fulfillment_date > max_date:
        raise ValueError(
            f"Tanggal pesanan maksimal 30 hari dari hari ini (paling lambat {max_date.isoformat()}). "
            f"Tanggal yang diminta: {fulfillment_date.isoformat()}."
        )

    return fulfillment_dt
