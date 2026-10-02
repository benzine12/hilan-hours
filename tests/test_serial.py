"""Calendar day serials used by Hilan's __calendarSelectedDays field."""
from datetime import date

import pytest

from hilan.serial import EPOCH, date_to_serial, month_serials, serial_to_date


class TestKnownValues:
    """Anchors read straight off the live page's days= attributes."""

    @pytest.mark.parametrize(
        "day, serial",
        [
            (date(2026, 9, 1), 9740),
            (date(2026, 9, 22), 9761),
            (date(2026, 9, 30), 9769),
            (date(2026, 8, 1), 9709),
            (date(2026, 8, 31), 9739),
            (EPOCH, 0),
        ],
    )
    def test_round_trip(self, day, serial):
        assert date_to_serial(day) == serial
        assert serial_to_date(serial) == day


class TestMonthSerials:
    def test_september_2026_is_thirty_days(self):
        s = month_serials(2026, 9)
        assert s == list(range(9740, 9770))
        assert len(s) == 30

    def test_august_2026_is_thirty_one_days(self):
        assert month_serials(2026, 8) == list(range(9709, 9740))

    def test_february_non_leap(self):
        assert len(month_serials(2026, 2)) == 28

    def test_february_leap(self):
        assert len(month_serials(2024, 2)) == 29

    def test_december_rolls_into_next_year(self):
        s = month_serials(2026, 12)
        assert len(s) == 31
        assert serial_to_date(s[-1]) == date(2026, 12, 31)

    def test_months_are_contiguous(self):
        assert month_serials(2026, 8)[-1] + 1 == month_serials(2026, 9)[0]


class TestBoundaries:
    def test_before_epoch_is_negative(self):
        assert date_to_serial(date(1999, 12, 31)) == -1

    @pytest.mark.parametrize("month", [0, 13, -1])
    def test_invalid_month_rejected(self, month):
        with pytest.raises(ValueError):
            month_serials(2026, month)
