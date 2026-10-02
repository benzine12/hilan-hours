# -*- coding: utf-8 -*-
"""The days the clocks move, and why nothing is corrected for them.

Israel moves its clocks on the Friday before the last Sunday of March (2026:
27/03, 02:00 becomes 03:00) and back on the last Sunday of October (2026: 25/10,
02:00 becomes 01:00). A session spanning either one has a wall-clock length that
differs by an hour from the time actually elapsed.

Hilan measures wall clock: a row's own total equals exit - entry with no
adjustment, including in the week of the switch. Its figure is what payroll
pays, so matching it is the whole job — "correcting" the hour would put this
tool a full hour away from the employer's own number and call that accuracy.

The spring switch lands on a Friday, which is not a working day.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from hilan.calc import analyse, hrs
from hilan.parser import DayRecord, MonthReport, ReportSegment

SPRING_FORWARD = date(2026, 3, 27)     # 02:00 -> 03:00, the day loses an hour
FALL_BACK = date(2026, 10, 25)         # 02:00 -> 01:00, the day gains one


def segment(entry, exit_, total=None, standard=None):
    return ReportSegment(entry=entry, exit=exit_, total=total,
                         standard=standard, comment="", symbol="נוכחות")


def month_of(day, segments, *, synced):
    """A one-day report, enough to exercise the arithmetic."""
    record = DayRecord(date=day, special=None, error=None, clock=[],
                       report=segments, is_error_day=False,
                       is_absence_day=False, calendar_label="")
    return MonthReport(
        year=day.year, month=day.month, employee_name="", employee_number="1",
        days=[record], totals=None, totals_match_month=False,
        clock_synced_at=synced,
    )


class TestHilansOwnFigureWins:
    """Where Hilan states the total, that is used and no arithmetic happens."""

    def test_a_session_across_the_spring_change(self):
        # 23:00-03:00 reads as four hours on the wall and three were lived.
        report = month_of(
            SPRING_FORWARD,
            [segment(time(23, 0), time(3, 0), total=timedelta(hours=4))],
            synced=datetime(2026, 3, 29, 9, 0),
        )
        a = analyse([report], today=date(2026, 3, 29),
                    now=datetime(2026, 3, 29, 9, 0))
        assert a.days[0].worked == timedelta(hours=4)

    def test_a_session_across_the_autumn_change(self):
        report = month_of(
            FALL_BACK,
            [segment(time(23, 0), time(3, 0), total=timedelta(hours=4))],
            synced=datetime(2026, 10, 27, 9, 0),
        )
        a = analyse([report], today=date(2026, 10, 27),
                    now=datetime(2026, 10, 27, 9, 0))
        assert a.days[0].worked == timedelta(hours=4)


class TestWithoutAStatedTotal:
    """Then it is computed, and on the same wall-clock basis Hilan uses."""

    def test_the_wall_clock_difference_is_taken(self):
        report = month_of(
            SPRING_FORWARD, [segment(time(23, 0), time(3, 0))],
            synced=datetime(2026, 3, 29, 9, 0),
        )
        a = analyse([report], today=date(2026, 3, 29),
                    now=datetime(2026, 3, 29, 9, 0))
        assert a.days[0].worked == timedelta(hours=4)

    def test_an_ordinary_day_is_unaffected(self):
        report = month_of(
            date(2026, 3, 25), [segment(time(9, 0), time(17, 20))],
            synced=datetime(2026, 3, 26, 9, 0),
        )
        a = analyse([report], today=date(2026, 3, 26),
                    now=datetime(2026, 3, 26, 9, 0))
        assert a.days[0].worked == timedelta(hours=8, minutes=20)


class TestARunningSessionOnASwitchDay:
    def test_the_hours_so_far_follow_the_wall_clock(self):
        """Clocked in at 00:30, asked at 03:30, across the 02:00 change.

        Two hours were lived and the wall says three. Three is the answer,
        because three is what Hilan will write down.
        """
        report = month_of(
            SPRING_FORWARD, [segment(time(0, 30), None)],
            synced=datetime(2026, 3, 27, 0, 0),
        )
        a = analyse([report], today=SPRING_FORWARD,
                    now=datetime(2026, 3, 27, 3, 30))
        assert a.forecast.worked_so_far == timedelta(hours=3)

    def test_the_punch_is_dated_by_its_row_not_by_the_clock(self):
        """A pretend date must not move where the session began."""
        report = month_of(
            date(2026, 3, 25), [segment(time(9, 0), None)],
            synced=datetime(2026, 3, 25, 0, 0),
        )
        a = analyse([report], today=date(2026, 3, 25),
                    now=datetime(2026, 3, 25, 12, 0))
        assert a.forecast.worked_so_far == timedelta(hours=3)


class TestTheSwitchDayItself:
    def test_a_friday_switch_requires_nothing(self):
        """27/03/2026 is a Friday, which is why the change is put there."""
        assert SPRING_FORWARD.isoweekday() == 5
        report = month_of(SPRING_FORWARD, [], synced=datetime(2026, 3, 29, 9, 0))
        a = analyse([report], today=date(2026, 3, 29),
                    now=datetime(2026, 3, 29, 9, 0))
        assert a.days[0].standard == timedelta(0)

    def test_the_autumn_one_lands_on_a_working_sunday(self):
        """25/10/2026 is a Sunday and does require a full day."""
        assert FALL_BACK.isoweekday() == 7
        report = month_of(FALL_BACK, [], synced=datetime(2026, 10, 27, 9, 0))
        a = analyse([report], today=date(2026, 10, 27),
                    now=datetime(2026, 10, 27, 9, 0))
        assert a.days[0].standard == timedelta(hours=9)
