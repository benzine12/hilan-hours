"""The required-hours rule, checked against Hilan's own totals."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from hilan.standard import (
    CHOL_HAMOED,
    EREV_CHAG,
    HOLIDAY,
    daily_standard,
    hours,
    month_standard,
    standard_through,
)

# Specials for September 2026, as Hilan labels them.
SEPT_2026 = {
    date(2026, 9, 11): EREV_CHAG,
    date(2026, 9, 13): HOLIDAY,
    date(2026, 9, 20): 'ערב יוה"כ',
    date(2026, 9, 21): HOLIDAY,
    date(2026, 9, 25): EREV_CHAG,
    date(2026, 9, 27): CHOL_HAMOED,
    date(2026, 9, 28): CHOL_HAMOED,
    date(2026, 9, 29): CHOL_HAMOED,
    date(2026, 9, 30): CHOL_HAMOED,
}


class TestGoldenAgainstHilan:
    """These two numbers are what Hilan itself prints for September 2026."""

    def test_month_total_matches_taken(self):
        assert hours(month_standard(2026, 9, SEPT_2026)) == Decimal("173.00")

    def test_through_today_matches_taken_ad_hayom(self):
        # Hilan's "תקן עד היום" on 22/09 includes that day in full.
        got = standard_through(2026, 9, date(2026, 9, 22), SEPT_2026)
        assert hours(got) == Decimal("119.50")


class TestWeekdayRule:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (date(2026, 9, 6), "9.00"),   # Sunday
            (date(2026, 9, 7), "9.00"),   # Monday
            (date(2026, 9, 8), "9.00"),   # Tuesday
            (date(2026, 9, 9), "9.00"),   # Wednesday
            (date(2026, 9, 10), "8.50"),  # Thursday
            (date(2026, 9, 4), "0.00"),   # Friday
            (date(2026, 9, 5), "0.00"),   # Saturday
        ],
    )
    def test_plain_days(self, day, expected):
        assert hours(daily_standard(day, None)) == Decimal(expected)


class TestSpecials:
    def test_holiday_is_zero_even_on_a_workday(self):
        assert daily_standard(date(2026, 9, 21), HOLIDAY) == timedelta(0)

    def test_erev_chag_is_four_hours(self):
        assert hours(daily_standard(date(2026, 9, 20), 'ערב יוה"כ')) == Decimal("4.00")

    def test_any_erev_variant_is_four_hours(self):
        for label in ["ערב חג", 'ערב יוה"כ', "ערב ראש השנה"]:
            assert hours(daily_standard(date(2026, 9, 20), label)) == Decimal("4.00")

    def test_erev_chag_on_friday_stays_zero(self):
        # Friday wins: the day is off regardless of the holiday label.
        assert daily_standard(date(2026, 9, 11), EREV_CHAG) == timedelta(0)

    def test_chol_hamoed_is_an_ordinary_workday(self):
        assert hours(daily_standard(date(2026, 9, 28), CHOL_HAMOED)) == Decimal("9.00")

    def test_chol_hamoed_on_thursday_keeps_the_short_day(self):
        assert hours(daily_standard(date(2026, 9, 24), CHOL_HAMOED)) == Decimal("8.50")

    def test_unknown_label_falls_back_to_weekday(self):
        assert hours(daily_standard(date(2026, 9, 8), "משהו חדש")) == Decimal("9.00")


class TestMonthAggregate:
    def test_august_2026_has_no_holidays(self):
        # 18 full days + 4 Thursdays.
        assert hours(month_standard(2026, 8, {})) == Decimal("196.00")

    def test_specials_outside_the_month_are_ignored(self):
        assert month_standard(2026, 8, SEPT_2026) == month_standard(2026, 8, {})

    def test_through_before_month_start_is_zero(self):
        assert standard_through(2026, 9, date(2026, 8, 31), SEPT_2026) == timedelta(0)

    def test_through_last_day_equals_full_month(self):
        assert standard_through(
            2026, 9, date(2026, 9, 30), SEPT_2026
        ) == month_standard(2026, 9, SEPT_2026)

    def test_through_after_month_end_equals_full_month(self):
        assert standard_through(2026, 9, date(2026, 10, 5), SEPT_2026) == month_standard(
            2026, 9, SEPT_2026
        )

    def test_through_first_day_of_month(self):
        assert hours(standard_through(2026, 9, date(2026, 9, 1), SEPT_2026)) == Decimal("9.00")


class TestHoursHelper:
    @pytest.mark.parametrize(
        "td, expected",
        [
            (timedelta(hours=9), "9.00"),
            (timedelta(hours=8, minutes=30), "8.50"),
            (timedelta(0), "0.00"),
            (timedelta(hours=106, minutes=15), "106.25"),
            (timedelta(hours=-1, minutes=-30), "-1.50"),
        ],
    )
    def test_decimal_hours(self, td, expected):
        assert hours(td) == Decimal(expected)
