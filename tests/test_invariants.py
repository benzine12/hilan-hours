# -*- coding: utf-8 -*-
"""Properties that must hold on every date and at every hour, not just today.

A tool that is right on the day it was written and wrong a week later is worse
than no tool. Rather than check one more example, these sweep every day of the
month against every hour of the clock and assert the things that can never be
allowed to vary.
"""
from datetime import date, datetime, time, timedelta

import pytest

from hilan.calc import analyse
from hilan.parser import parse_month

HOURS = [0, 6, 8, 12, 17, 23]


def days_of(year, month):
    import calendar

    last = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, last + 1)]


def sweep(html, year, month, extra_days=()):
    """Every day of the month at several hours, plus any extra dates given.

    The report is parsed once and shared: analyse only reads it, and parsing it
    afresh for each of two hundred combinations would cost more than the sweep.
    """
    report = parse_month(html)
    for day in list(days_of(year, month)) + list(extra_days):
        for hour in HOURS:
            yield report, day, datetime.combine(day, time(hour, 0))


@pytest.fixture(scope="session")
def sweeps(september_html, august_html):
    return (
        list(sweep(september_html, 2026, 9,
                   extra_days=[date(2026, 8, 30), date(2026, 10, 3), date(2027, 1, 1)])),
        list(sweep(august_html, 2026, 8, extra_days=[date(2026, 9, 23)])),
    )


def every(sweeps):
    for group in sweeps:
        for report, today, now in group:
            yield analyse([report], today=today, now=now), today, now


class TestItNeverCrashes:
    def test_every_day_and_hour_produces_an_analysis(self, sweeps):
        count = sum(1 for _ in every(sweeps))
        assert count == len(sweeps[0]) + len(sweeps[1])
        assert count > 200


class TestTheCutIsAlwaysBehindToday:
    def test_today_is_never_settled(self, sweeps):
        for a, today, now in every(sweeps):
            assert a.settled_through < today, (today, now, a.settled_through)

    def test_the_cut_never_passes_the_clock_marker(self, sweeps):
        for a, today, now in every(sweeps):
            synced = a.report.clock_synced_at
            if synced:
                assert a.settled_through < synced.date(), (today, a.settled_through)


class TestBalancesAddUp:
    def test_every_period_balance_is_its_own_parts(self, sweeps):
        for a, today, now in every(sweeps):
            for name in ("now", "week", "month"):
                p = getattr(a, name)
                assert p.balance == p.worked + p.credited - p.standard, (name, today)

    def test_the_banked_figure_is_the_sum_of_the_settled_days(self, sweeps):
        for a, today, now in every(sweeps):
            counted = [d for d in a.days if d.date <= a.settled_through]
            assert a.now.worked == sum((d.worked for d in counted), timedelta(0))
            assert a.now.standard == sum((d.standard for d in counted), timedelta(0))
            assert a.now.balance == sum((d.balance for d in counted), timedelta(0))

    def test_the_month_total_covers_every_day_of_it(self, sweeps):
        for a, today, now in every(sweeps):
            assert a.month.worked == sum((d.worked for d in a.days), timedelta(0))


class TestTheClockDoesNotMoveTheBank:
    """The banked figure must depend on the date alone, never on the hour."""

    def test_the_hour_of_the_day_changes_nothing(self, september_html):
        for day in days_of(2026, 9):
            report = parse_month(september_html)
            seen = {
                analyse([report], today=day,
                        now=datetime.combine(day, time(h, 0))).now.balance
                for h in range(0, 24, 3)
            }
            assert len(seen) == 1, (day, seen)

    def test_nor_does_it_move_the_cut(self, september_html):
        for day in days_of(2026, 9):
            report = parse_month(september_html)
            seen = {
                analyse([report], today=day,
                        now=datetime.combine(day, time(h, 0))).settled_through
                for h in range(0, 24, 3)
            }
            assert len(seen) == 1, (day, seen)


class TestTheForecastBehaves:
    def test_no_entry_means_no_leave_time(self, sweeps):
        for a, today, now in every(sweeps):
            f = a.forecast
            if f is not None and f.entry is None:
                assert f.leave_to_close_today is None
                assert f.leave_to_end_level is None

    def test_remaining_is_never_negative(self, sweeps):
        for a, today, now in every(sweeps):
            if a.forecast is not None:
                assert a.forecast.remaining >= timedelta(0), today

    def test_worked_so_far_includes_the_earlier_hours(self, sweeps):
        for a, today, now in every(sweeps):
            f = a.forecast
            if f is not None:
                assert f.worked_so_far >= f.earlier_today, today

    def test_the_leave_time_does_not_drift_through_the_day(self, september_html):
        """Asked at 09:00 and at 16:00, the answer has to be the same."""
        from tests.test_forecast import september_on_the_23rd, with_entry

        seen = set()
        for hour in range(9, 20):
            r = with_entry(september_on_the_23rd(september_html), 8, 20)
            a = analyse([r], today=date(2026, 9, 23),
                        now=datetime(2026, 9, 23, hour, 0))
            seen.add(a.forecast.leave_to_close_today)
        assert seen == {time(16, 35)}


class TestIssuesStayHonest:
    def test_nothing_past_the_cut_is_ever_actionable(self, sweeps):
        """A day that has not settled cannot be something to go and fix."""
        for a, today, now in every(sweeps):
            for issue in a.issues:
                if issue.date > a.settled_through:
                    assert not issue.actionable, (today, issue)

    def test_no_issue_is_raised_for_a_future_date(self, sweeps):
        for a, today, now in every(sweeps):
            for issue in a.issues:
                assert issue.date <= today, (today, issue)


class TestTheOutputSurvivesEveryDay:
    """The screen has to survive every day, not only the arithmetic.

    A day with no punches, a day whose forecast is None, a past month, a
    Saturday — each takes a different path through the summary.
    """

    def drawn(self, analysis, fn):
        from rich.console import Console

        console = Console(record=True, width=100, force_terminal=False)
        fn(analysis, console)
        return console.export_text()

    def test_the_summary_draws_for_every_day_and_hour(self, sweeps):
        from hilan.render import render_summary

        for a, today, now in every(sweeps):
            self.drawn(a, render_summary)      # must not raise

    def test_so_does_the_brief_screen(self, sweeps):
        from hilan.render import render_brief

        for a, today, now in every(sweeps):
            self.drawn(a, render_brief)

    def test_so_does_the_day_table(self, sweeps):
        from hilan.render import render_days

        for a, today, now in every(sweeps):
            self.drawn(a, render_days)

    def test_nothing_ever_overflows_eighty_columns(self, sweeps):
        from hilan.render import render_brief, render_summary

        for a, today, now in every(sweeps):
            for fn in (render_summary, render_brief):
                for line in self.drawn(a, fn).splitlines():
                    assert len(line.rstrip()) <= 80, (today, now, len(line), line)

    def test_the_json_is_serialisable_every_day(self, sweeps):
        import json

        from hilan.render import to_dict

        for a, today, now in every(sweeps):
            json.dumps(to_dict(a), ensure_ascii=False)

    def test_a_day_with_no_punches_still_draws(self, september_html):
        from hilan.render import render_summary

        report = parse_month(september_html)
        for day in report.days:
            day.report, day.clock = [], []
        for hour in HOURS:
            a = analyse([report], today=date(2026, 9, 14),
                        now=datetime(2026, 9, 14, hour, 0))
            assert "TODAY" in self.drawn(a, render_summary)

    def test_a_past_month_has_no_forecast_and_draws_anyway(self, august_html):
        from hilan.render import render_summary

        a = analyse([parse_month(august_html)], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 12, 0))
        assert a.forecast is None
        assert "BANKED" in self.drawn(a, render_summary)
