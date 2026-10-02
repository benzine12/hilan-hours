# -*- coding: utf-8 -*-
"""When can I leave today, and what are today's hours actually worth?

Today is never settled, so it stays out of the banked balance — but the hours are
real and the question "when can I go home" is the one asked most often. This is
that answer, kept separate from the banked balance so neither can distort the
other.
"""
from datetime import date, datetime, time, timedelta

import pytest

from hilan.calc import analyse
from hilan.parser import parse_month

TODAY = date(2026, 9, 23)


def at(h, m):
    return datetime(2026, 9, 23, h, m)


def september_on_the_23rd(html):
    """The capture is from the 22nd; move it on to the morning of the 23rd.

    22/09 was still open when the fixture was taken, so close it at 18:05, and
    give 23/09 an early row of 06:00-06:45. The clock marker moves with it.
    """
    from hilan.parser import ReportSegment

    r = parse_month(html)
    r.clock_synced_at = r.clock_synced_at.replace(day=23, hour=7, minute=0)

    d22 = next(d for d in r.days if d.date == date(2026, 9, 22))
    d22.report = [
        ReportSegment(entry=time(8, 15), exit=time(18, 5),
                      total=timedelta(hours=9, minutes=50),
                      standard=None, comment="", symbol="נוכחות")
    ]
    d23 = next(d for d in r.days if d.date == TODAY)
    d23.report = [
        ReportSegment(entry=time(6, 0), exit=time(6, 45),
                      total=timedelta(minutes=45),
                      standard=None, comment="", symbol="נוכחות")
    ]
    return r


@pytest.fixture
def report(september_html):
    return september_on_the_23rd(september_html)


def with_entry(report, hour, minute):
    """Add an open דיווח row for today, the way a morning punch arrives."""
    from hilan.parser import ReportSegment

    day = next(d for d in report.days if d.date == TODAY)
    day.report.append(
        ReportSegment(entry=time(hour, minute), exit=None, total=None,
                      standard=None, comment=None, symbol="נוכחות")
    )
    return report


class TestLeaveTime:
    def test_it_counts_from_the_entry_and_what_is_already_banked_today(self, report):
        """0.75 already on the clock, 9.00 required, in at 08:20 -> out at 16:35."""
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.leave_to_close_today == time(16, 35)

    def test_the_answer_does_not_drift_as_the_day_goes_on(self, report):
        a1 = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(9, 0))
        r2 = with_entry(september_on_the_23rd(rebuilt_html()), 8, 20)
        a2 = analyse([r2], today=TODAY, now=at(15, 30))
        assert a1.forecast.leave_to_close_today == a2.forecast.leave_to_close_today

    def test_spending_the_surplus_allows_an_earlier_exit(self, report):
        """Banked +5.58 against a 9.00 day: only 3.42 of it is needed, 0.75 is done."""
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(9, 0))
        assert a.forecast.leave_to_end_level == time(11, 0)

    def test_worked_so_far_includes_the_open_segment(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(12, 0))
        # 0:45 closed + 3:40 open = 4:25
        assert a.forecast.worked_so_far == timedelta(hours=4, minutes=25)

    def test_it_reports_what_is_still_missing(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.remaining == timedelta(hours=4, minutes=35)

    def test_nothing_remains_once_the_day_is_made(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(18, 0))
        assert a.forecast.remaining == timedelta(0)
        assert a.forecast.done_for_today is True


class TestWithoutAnOpenPunch:
    def test_no_leave_time_is_invented(self, report):
        """The morning punch takes a couple of hours to arrive; do not guess."""
        a = analyse([report], today=TODAY, now=at(12, 0))
        assert a.forecast.entry is None
        assert a.forecast.leave_to_close_today is None

    def test_the_hours_already_recorded_still_count(self, report):
        a = analyse([report], today=TODAY, now=at(12, 0))
        assert a.forecast.worked_so_far == timedelta(minutes=45)
        assert a.forecast.remaining == timedelta(hours=8, minutes=15)

    def test_an_entry_can_be_supplied_by_hand(self, report):
        a = analyse([report], today=TODAY, now=at(12, 0), assumed_entry=time(8, 20))
        assert a.forecast.entry == time(8, 20)
        assert a.forecast.leave_to_close_today == time(16, 35)
        assert a.forecast.entry_assumed is True


class TestTodayDoesNotLeakIntoTheBankedBalance:
    def test_the_settled_balance_is_untouched(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(17, 0))
        assert a.settled_through == date(2026, 9, 22)
        assert a.now.worked == timedelta(hours=116, minutes=5)


class TestAZeroRequirementDay:
    def test_a_day_off_asks_for_nothing(self, report):
        a = analyse([report], today=date(2026, 9, 25), now=datetime(2026, 9, 25, 10, 0))
        assert a.forecast.required == timedelta(0)
        assert a.forecast.done_for_today is True


def rebuilt_html():
    from tests.capture import SEPTEMBER
    from tests.conftest import build_html

    return build_html(SEPTEMBER)


class TestTheRawClockPunchIsUsedToo:
    """דיווחי שעון carries the punch before דיווח is built from it.

    Looking only at דיווח would say "not clocked in" while the entry sits right
    there in the clock column, and ask for --in to be typed for something the
    tool can already see.
    """

    def clocked_in(self, report, hour, minute):
        from hilan.parser import ClockSegment

        day = next(d for d in report.days if d.date == TODAY)
        day.clock = [ClockSegment(entry=time(hour, minute), exit=None)]
        return report

    def test_an_open_clock_punch_counts_as_being_in(self, report):
        a = analyse([self.clocked_in(report, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.entry == time(8, 20)
        assert a.forecast.entry_assumed is False

    def test_it_gives_the_same_leave_time(self, report):
        a = analyse([self.clocked_in(report, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.leave_to_close_today == time(16, 35)

    def test_a_closed_clock_punch_is_not_an_open_day(self, report):
        from hilan.parser import ClockSegment

        day = next(d for d in report.days if d.date == TODAY)
        day.clock = [ClockSegment(entry=time(8, 20), exit=time(17, 0))]
        a = analyse([report], today=TODAY, now=at(19, 0))
        assert a.forecast.entry is None

    def test_the_reported_row_wins_when_both_are_there(self, report):
        """דיווח is the source of truth; the clock is only the earlier sighting."""
        r = with_entry(self.clocked_in(report, 6, 0), 8, 20)
        a = analyse([r], today=TODAY, now=at(12, 0))
        assert a.forecast.entry == time(8, 20)

    def test_a_supplied_entry_still_loses_to_a_real_punch(self, report):
        a = analyse([self.clocked_in(report, 8, 20)], today=TODAY,
                    now=at(12, 0), assumed_entry=time(6, 0))
        assert a.forecast.entry == time(8, 20)
        assert a.forecast.entry_assumed is False


class TestHoursEarlierInTheDayAreVisible:
    """08:20 + 9.00 is 17:20, so a leave time of 16:35 needs explaining.

    The 45 minutes worked at 06:00 belong to the same day and count towards its
    requirement, so the leave time is right — but a leave time that cannot be
    checked against a clock and a requirement is a number to be taken on trust,
    which is not what this tool is for.
    """

    def test_the_earlier_hours_are_reported(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.earlier_today == timedelta(minutes=45)

    def test_they_explain_the_gap_to_entry_plus_the_requirement(self, report):
        f = analyse([with_entry(report, 8, 20)], today=TODAY, now=at(12, 0)).forecast
        naive = _plus_hours(f.entry, f.required)
        assert naive == time(17, 20)
        assert f.leave_to_close_today == time(16, 35)
        assert f.earlier_today == timedelta(minutes=45)

    def test_a_day_with_no_earlier_hours_reports_none(self, september_html):
        r = september_on_the_23rd(september_html)
        day = next(d for d in r.days if d.date == TODAY)
        day.report = []
        a = analyse([with_entry(r, 8, 20)], today=TODAY, now=at(12, 0))
        assert a.forecast.earlier_today == timedelta(0)
        assert a.forecast.leave_to_close_today == time(17, 20)


def _plus_hours(start, delta):
    from datetime import datetime as _dt

    return (_dt.combine(date(2000, 1, 1), start) + delta).time()


class TestACoveredTurnstilePunchIsNotARunningSession:
    """A dangling turnstile entry is only "still at work" if nothing closed it.

    A turnstile entry at 08:40 with no exit, inside a דיווח row of 08:40-17:40
    that closes the day. Reading the entry as a running session would add the
    whole afternoon on top of a finished day, and offer a leave time of 08:40.
    """

    def with_clock_and_report(self, report, clock_entry, rows):
        from hilan.parser import ClockSegment

        day = next(d for d in report.days if d.date == TODAY)
        day.clock = [ClockSegment(entry=clock_entry, exit=None)]
        day.report = [
            ReportSegment(entry=a, exit=b, total=c, standard=None, comment="",
                          symbol="נוכחות")
            for a, b, c in rows
        ]
        return report

    def test_a_punch_inside_a_closed_row_is_not_running(self, report):
        from hilan.parser import ReportSegment  # noqa: F401 — used by the helper

        r = self.with_clock_and_report(
            report, time(8, 40),
            [(time(8, 40), time(17, 40), timedelta(hours=9))],
        )
        f = analyse([r], today=TODAY, now=at(18, 30)).forecast
        assert f.entry is None
        assert f.worked_so_far == timedelta(hours=9)
        assert f.leave_to_close_today is None

    def test_the_live_balance_stops_growing(self, report):
        r = self.with_clock_and_report(
            report, time(8, 40),
            [(time(8, 40), time(17, 40), timedelta(hours=9))],
        )
        noon = analyse([r], today=TODAY, now=at(12, 30)).live_balance
        evening = analyse([r], today=TODAY, now=at(18, 30)).live_balance
        assert noon == evening

    def test_an_earlier_row_does_not_cover_a_later_punch(self, report):
        """An early row of 06:00-06:45 must not hide an 08:20 clock-in."""
        r = self.with_clock_and_report(
            report, time(8, 20),
            [(time(6, 0), time(6, 45), timedelta(minutes=45))],
        )
        f = analyse([r], today=TODAY, now=at(12, 0)).forecast
        assert f.entry == time(8, 20)
        assert f.leave_to_close_today == time(16, 35)


from hilan.parser import ReportSegment  # noqa: E402 — the class above needs it


class TestTodayAsksWhatHilanSays:
    """Today's requirement is Hilan's figure where it states one, as for every day."""

    def stated(self, report, value):
        # The row that stated the day's requirement is the one now being filled
        # in, so it is no longer an idle row as well.
        day = next(d for d in report.days if d.date == TODAY)
        day.report = [ReportSegment(entry=time(8, 20), exit=None, total=None,
                                    standard=Decimal(value), comment="", symbol="נוכחות")]
        day.idle_standards = []
        return report

    def test_the_requirement(self, report):
        a = analyse([self.stated(report, "6.00")], today=TODAY, now=at(12, 0))
        assert a.forecast.required == timedelta(hours=6)

    def test_the_leave_time_follows_it(self, report):
        a = analyse([self.stated(report, "6.00")], today=TODAY, now=at(12, 0))
        assert a.forecast.leave_to_close_today == time(14, 20)

    def test_the_live_balance_follows_it(self, report):
        a = analyse([self.stated(report, "6.00")], today=TODAY, now=at(14, 20))
        assert a.live_balance == a.now.balance

    def test_without_a_stated_figure_the_rule_decides(self, report):
        a = analyse([report], today=TODAY, now=at(12, 0))
        assert a.forecast.required == timedelta(hours=9)       # a Wednesday


class TestTheClockIsReadInWholeMinutes:
    def test_seconds_are_dropped(self, report):
        a = analyse([with_entry(report, 8, 20)], today=TODAY,
                    now=datetime(2026, 9, 23, 12, 0, 59, 999999))
        assert a.forecast.worked_so_far == timedelta(hours=4, minutes=25)
        assert a.clock == datetime(2026, 9, 23, 12, 0)


from decimal import Decimal  # noqa: E402


class TestTodaysLeaveCounts:
    """Leave already credited today is part of what today has done."""

    def on(self, report, segments):
        day = next(d for d in report.days if d.date == TODAY)
        day.report, day.clock, day.idle_standards = segments, [], []
        return report

    def test_a_day_of_leave_is_done(self, report):
        r = self.on(report, [ReportSegment(entry=None, exit=None, total=None,
                                           standard=Decimal("9.00"), comment="", symbol="חופשה")])
        f = analyse([r], today=TODAY, now=at(10, 0)).forecast
        assert f.credited == timedelta(hours=9)
        assert f.remaining == timedelta(0) and f.done_for_today

    def test_half_a_day_of_leave_leaves_half_to_work(self, report):
        r = self.on(report, [
            ReportSegment(entry=time(8, 0), exit=time(12, 30), total=timedelta(hours=4, minutes=30),
                          standard=Decimal("9.00"), comment="", symbol="נוכחות"),
            ReportSegment(entry=None, exit=None, total=None, standard=Decimal("4.50"),
                          comment="", symbol="חופשה"),
        ])
        f = analyse([r], today=TODAY, now=at(13, 0)).forecast
        assert f.credited == timedelta(hours=4, minutes=30)
        assert f.done_for_today

    def test_the_leave_time_takes_the_leave_into_account(self, report):
        r = self.on(report, [
            ReportSegment(entry=time(8, 20), exit=None, total=None, standard=Decimal("9.00"),
                          comment="", symbol="נוכחות"),
            ReportSegment(entry=None, exit=None, total=None, standard=Decimal("4.00"),
                          comment="", symbol="חופשה"),
        ])
        f = analyse([r], today=TODAY, now=at(10, 0)).forecast
        assert f.leave_to_close_today == time(13, 20)        # 08:20 + 9.00 - 4.00

    def test_the_screen_says_so(self, report):
        from rich.console import Console
        from hilan.render import render_summary

        r = self.on(report, [ReportSegment(entry=None, exit=None, total=None,
                                           standard=Decimal("9.00"), comment="", symbol="חופשה")])
        console = Console(record=True, width=100, force_terminal=False)
        render_summary(analyse([r], today=TODAY, now=at(10, 0)), console)
        out = console.export_text()
        assert "9.00 leave" in out and "done for today" in out
        assert "--in" not in out
