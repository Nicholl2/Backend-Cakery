"""
Order date validation tests for Toti Cakery Backend.

Covers:
- Same-day → reject
- H-1 → reject (same as same-day for date-only)
- Tomorrow (H+1) → accept
- +30 days → accept
- +31 days → reject
- Month transition
- Year transition
- Leap year
- Timezone edge case (UTC vs WIB)
"""

import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.utils.business_date import (
    WIB,
    MIN_DAYS_AHEAD,
    MAX_DAYS_AHEAD,
    get_business_today,
    validate_fulfillment_date,
)


def _fixed_today(fixed_date: date):
    """Patch get_business_today to return a fixed date for deterministic tests."""
    return patch("app.utils.business_date.get_business_today", return_value=fixed_date)


class TestBusinessDateConstants:
    def test_min_days_ahead(self):
        assert MIN_DAYS_AHEAD == 1

    def test_max_days_ahead(self):
        assert MAX_DAYS_AHEAD == 30

    def test_wib_offset(self):
        assert WIB.utcoffset(None).total_seconds() == 7 * 3600


class TestGetBusinessToday:
    def test_returns_date(self):
        result = get_business_today()
        assert isinstance(result, date)

    def test_uses_wib_timezone(self):
        """When UTC is late evening, WIB should be next day."""
        # 2026-10-01 17:30 UTC = 2026-10-02 00:30 WIB
        fake_utc = datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc)
        with patch("app.utils.business_date.datetime") as mock_dt:
            mock_dt.now.return_value = fake_utc.astimezone(WIB)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_business_today()
            assert result == date(2026, 10, 2)


# ── CORE DATE VALIDATION ─────────────────────────────────────────────────────

class TestFulfillmentDateValidation:
    """Test validate_fulfillment_date with fixed 'today' = 2026-10-01."""

    FIXED_TODAY = date(2026, 10, 1)

    # Case 1 — Same day → reject
    def test_same_day_rejected(self):
        with _fixed_today(self.FIXED_TODAY):
            with pytest.raises(ValueError, match="minimal H"):
                validate_fulfillment_date(
                    datetime(2026, 10, 1, 10, 0, tzinfo=WIB)
                )

    # Case 2 — Tomorrow → accept
    def test_tomorrow_accepted(self):
        with _fixed_today(self.FIXED_TODAY):
            result = validate_fulfillment_date(
                datetime(2026, 10, 2, 10, 0, tzinfo=WIB)
            )
            assert result is not None

    # Case 3 — Exactly 30 days → accept
    def test_exactly_30_days_accepted(self):
        with _fixed_today(self.FIXED_TODAY):
            target = datetime(2026, 10, 31, 10, 0, tzinfo=WIB)
            result = validate_fulfillment_date(target)
            assert result is not None

    # Case 4 — 31 days → reject
    def test_31_days_rejected(self):
        with _fixed_today(self.FIXED_TODAY):
            with pytest.raises(ValueError, match="maksimal 30 hari"):
                validate_fulfillment_date(
                    datetime(2026, 11, 1, 10, 0, tzinfo=WIB)
                )

    # Case 5 — Month transition
    def test_month_transition(self):
        fixed = date(2026, 10, 28)
        with _fixed_today(fixed):
            # 28 Oct + 30 = 27 Nov → should be accepted
            result = validate_fulfillment_date(
                datetime(2026, 11, 27, 10, 0, tzinfo=WIB)
            )
            assert result is not None

            # 28 Oct + 31 = 28 Nov → should be rejected
            with pytest.raises(ValueError):
                validate_fulfillment_date(
                    datetime(2026, 11, 28, 10, 0, tzinfo=WIB)
                )

    # Case 6 — Year transition
    def test_year_transition(self):
        fixed = date(2026, 12, 20)
        with _fixed_today(fixed):
            # 20 Dec + 30 = 19 Jan → accepted
            result = validate_fulfillment_date(
                datetime(2027, 1, 19, 10, 0, tzinfo=WIB)
            )
            assert result is not None

            # 20 Dec + 31 = 20 Jan → rejected
            with pytest.raises(ValueError):
                validate_fulfillment_date(
                    datetime(2027, 1, 20, 10, 0, tzinfo=WIB)
                )

    # Case 7 — Leap year
    def test_leap_year(self):
        # 2028 is a leap year
        fixed = date(2028, 2, 27)
        with _fixed_today(fixed):
            # 27 Feb + 1 = 28 Feb → accepted
            result = validate_fulfillment_date(
                datetime(2028, 2, 28, 10, 0, tzinfo=WIB)
            )
            assert result is not None

            # 27 Feb + 2 = 29 Feb (leap day) → accepted
            result = validate_fulfillment_date(
                datetime(2028, 2, 29, 10, 0, tzinfo=WIB)
            )
            assert result is not None


# ── TIMEZONE EDGE CASES ──────────────────────────────────────────────────────

class TestTimezoneEdgeCases:
    def test_utc_evening_is_next_day_wib(self):
        """
        Server UTC: 2026-10-01 17:30
        Indonesia:  2026-10-02 00:30
        Business date should be Oct 2, so Oct 2 fulfillment should be accepted.
        """
        fixed = date(2026, 10, 2)  # Business today = Oct 2 in WIB
        with _fixed_today(fixed):
            # Oct 3 (tomorrow in WIB) → accepted
            result = validate_fulfillment_date(
                datetime(2026, 10, 3, 10, 0, tzinfo=WIB)
            )
            assert result is not None

            # Oct 2 (today in WIB) → rejected
            with pytest.raises(ValueError):
                validate_fulfillment_date(
                    datetime(2026, 10, 2, 10, 0, tzinfo=WIB)
                )

    def test_naive_datetime_assumed_wib(self):
        """Naive datetimes should be treated as WIB."""
        fixed = date(2026, 10, 1)
        with _fixed_today(fixed):
            result = validate_fulfillment_date(
                datetime(2026, 10, 2, 10, 0)  # naive
            )
            assert result is not None

    def test_utc_datetime_converted_to_wib(self):
        """UTC datetime should be converted to WIB for date comparison."""
        fixed = date(2026, 10, 1)
        with _fixed_today(fixed):
            # 2026-10-01 20:00 UTC = 2026-10-02 03:00 WIB → date is Oct 2 → accepted
            result = validate_fulfillment_date(
                datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
            )
            assert result is not None
