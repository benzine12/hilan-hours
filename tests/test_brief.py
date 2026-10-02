# -*- coding: utf-8 -*-
"""The brief screen: hours actually done, and hours in the bank.

Everything else stays on the full screen; these two numbers are what gets looked
at day to day.
"""
from datetime import date, datetime, time, timedelta

import pytest
from rich.console import Console

from hilan.calc import analyse
from hilan.parser import parse_month
from hilan.render import render_brief

TODAY = date(2026, 9, 23)


def text_of(analysis) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    render_brief(analysis, console)
    return console.export_text()


@pytest.fixture
def morning(september_html):
    from tests.test_forecast import september_on_the_23rd, with_entry

    r = with_entry(september_on_the_23rd(september_html), 8, 20)
    return analyse([r], today=TODAY, now=datetime(2026, 9, 23, 12, 0))


@pytest.fixture
def quiet(september_html):
    """A month with nothing to flag."""
    from tests.test_forecast import september_on_the_23rd

    r = september_on_the_23rd(september_html)
    for day in r.days:
        day.error = None
        day.is_error_day = False
        day.clock = []
    return analyse([r], today=TODAY, now=datetime(2026, 9, 23, 12, 0))


class TestTheTwoNumbers:
    def test_hours_actually_done_this_month(self, morning):
        line = next(l for l in text_of(morning).splitlines() if "WORKED" in l)
        assert "116.83" in line          # 116.08 settled + 0.75 recorded today

    def test_hours_in_the_bank_right_now(self, morning):
        """5.58 settled, 4.42 of a 9.00 day done: 1.00 if you stopped here."""
        line = next(l for l in text_of(morning).splitlines() if "BANKED" in l)
        assert "+1.00" in line
        assert "5.58 settled" in line

    def test_the_bank_says_what_date_it_counts_to(self, morning):
        line = next(l for l in text_of(morning).splitlines() if "BANKED" in l)
        assert "22/09" in line

    def test_a_running_session_is_shown_but_kept_separate(self, morning):
        """3:40 since 08:20 is real, but Hilan has not recorded it yet."""
        line = next(l for l in text_of(morning).splitlines() if "WORKED" in l)
        assert "3.67" in line and "running" in line


class TestNothingElse:
    @pytest.mark.parametrize(
        "noise", ["WEEK", "MONTH", "TODAY", "Clock synced", "required", "credited"]
    )
    def test_the_rest_is_gone(self, morning, noise):
        assert noise not in text_of(morning)

    def test_it_fits_a_glance(self, morning):
        body = [l for l in text_of(morning).splitlines() if l.strip()]
        assert len(body) <= 4, body


class TestProblemsAreNotHiddenSilently:
    """Dropping a day that costs hours would defeat the whole tool."""

    def test_one_line_points_at_the_detail(self, morning):
        out = text_of(morning)
        assert "run: hilan" in out
        assert "08/09" not in out          # the detail itself stays behind the flag

    def test_nothing_is_said_when_there_is_nothing_to_say(self, quiet):
        assert "needs attention" not in text_of(quiet)


class TestAlignment:
    def test_the_two_numbers_line_up(self, morning):
        lines = text_of(morning).splitlines()
        worked = next(l for l in lines if "WORKED" in l)
        banked = next(l for l in lines if "BANKED" in l)
        assert worked.index("116.83") + len("116.83") == banked.index("+1.00") + len("+1.00")
