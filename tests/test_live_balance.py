# -*- coding: utf-8 -*-
"""What the bank holds at this minute, not what it held last night.

The banked figure stops at the last settled day, so on any working afternoon it
answers a question nobody asked: +5.58 while today is 2.50 short reads as "you
are 5.58 up" when walking out now would leave 3.08 (the numbers below).

Both numbers are true and neither replaces the other. The live one leads,
because "how am I doing right now" is the question, and it is labelled so it
cannot be read as the settled figure.
"""
from datetime import date, datetime, time, timedelta

import pytest

from hilan.calc import analyse
from hilan.parser import ReportSegment

TODAY = date(2026, 9, 23)


def with_day(report, punches):
    day = next(d for d in report.days if d.date == TODAY)
    day.report = [
        ReportSegment(entry=a, exit=b, total=c, standard=None, comment="",
                      symbol="נוכחות")
        for a, b, c in punches
    ]
    return report


@pytest.fixture
def afternoon(september_html):
    """Two finished sessions, 6.50 in all."""
    from tests.test_forecast import september_on_the_23rd

    return analyse(
        [with_day(september_on_the_23rd(september_html),
                  [(time(6, 0), time(6, 45), timedelta(minutes=45)),
                   (time(8, 20), time(14, 5), timedelta(hours=5, minutes=45))])],
        today=TODAY, now=datetime(2026, 9, 23, 17, 0),
    )


class TestTheLiveFigure:
    def test_it_is_the_banked_one_less_what_today_still_owes(self, afternoon):
        # 5.58 banked, 6.50 worked against a 9.00 day: 5.58 - 2.50
        assert afternoon.live_balance == timedelta(hours=3, minutes=5)

    def test_the_banked_figure_is_untouched(self, afternoon):
        assert afternoon.now.balance == timedelta(hours=5, minutes=35)

    def test_a_finished_day_makes_them_agree(self, september_html):
        from tests.test_forecast import september_on_the_23rd

        a = analyse(
            [with_day(september_on_the_23rd(september_html),
                      [(time(8, 0), time(17, 0), timedelta(hours=9))])],
            today=TODAY, now=datetime(2026, 9, 23, 17, 30),
        )
        assert a.live_balance == a.now.balance

    def test_working_past_the_day_puts_it_ahead(self, september_html):
        from tests.test_forecast import september_on_the_23rd

        a = analyse(
            [with_day(september_on_the_23rd(september_html),
                      [(time(8, 0), time(19, 0), timedelta(hours=11))])],
            today=TODAY, now=datetime(2026, 9, 23, 19, 30),
        )
        assert a.live_balance == a.now.balance + timedelta(hours=2)

    def test_a_running_session_counts_towards_it(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        report = with_entry(september_on_the_23rd(september_html), 8, 20)
        early = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 10, 0))
        later = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 14, 0))
        assert later.live_balance - early.live_balance == timedelta(hours=4)

    def test_a_day_off_costs_nothing(self, september_html):
        """25/09 is a Friday: nothing required, so nothing to lose by resting."""
        from tests.test_forecast import september_on_the_23rd

        a = analyse([september_on_the_23rd(september_html)],
                    today=date(2026, 9, 25), now=datetime(2026, 9, 25, 12, 0))
        assert a.live_balance == a.now.balance

    def test_leave_today_costs_nothing_either(self, september_html):
        """14/09 is a day of vacation."""
        from tests.test_forecast import september_on_the_23rd

        a = analyse([september_on_the_23rd(september_html)],
                    today=date(2026, 9, 14), now=datetime(2026, 9, 14, 12, 0))
        assert a.live_balance == a.now.balance

    def test_a_past_month_has_no_today_to_count(self, august_html):
        from hilan.parser import parse_month

        a = analyse([parse_month(august_html)], today=TODAY,
                    now=datetime(2026, 9, 23, 12, 0))
        assert a.live_balance == a.now.balance
