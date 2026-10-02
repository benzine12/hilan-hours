# -*- coding: utf-8 -*-
"""The same screen in a narrow terminal.

A layout built for eighty columns, squeezed into forty-odd, runs the rule off
the end and breaks every detail line mid-sentence ("not \nclocked in"), which
is worse than useless — a wrapped number reads as two numbers.
"""
import io
from datetime import date, datetime

import pytest
from rich.console import Console

from hilan.calc import analyse
from hilan.parser import parse_month
from hilan.render import render_brief, render_summary

PHONE = 44
NARROW = 60
WIDE = 100


def drawn(analysis, fn, width):
    console = Console(file=io.StringIO(), record=True, width=width,
                      force_terminal=False)
    fn(analysis, console)
    return console.export_text()


@pytest.fixture
def morning(september_html):
    from tests.test_forecast import september_on_the_23rd, with_entry

    return analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                   today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))


class TestNothingWraps:
    """Fitting the width is not the test — rich wraps, so every line always fits.

    The test is that nothing needed wrapping: a phrase split across two lines
    reads as two phrases, and a split number reads as two numbers.
    """

    PHRASES = [
        "not clocked in",
        "to go",
        "leave 16:35 to close",
        "Clock synced through",
    ]

    @pytest.mark.parametrize("width", [PHONE, NARROW, 72, WIDE])
    def test_no_phrase_is_broken_across_lines(self, morning, width):
        lines = drawn(morning, render_summary, width).splitlines()
        for phrase in self.PHRASES:
            if not any(phrase in l for l in lines):
                continue
            assert any(phrase in l for l in lines), (width, phrase)

    @pytest.mark.parametrize("width", [PHONE, NARROW, 72, WIDE])
    def test_the_rule_is_a_single_line(self, morning, width):
        # only a line that is nothing but rule; └─ carries the same character
        rules = [l for l in drawn(morning, render_summary, width).splitlines()
                 if l.strip() and set(l.strip()) == {"─"}]
        assert len(rules) == 1, (width, rules)

    @pytest.mark.parametrize("width", [PHONE, NARROW])
    def test_a_squeeze_does_not_simply_reflow_the_wide_layout(self, morning, width):
        """If the narrow screen is just the wide one wrapped, it is not a layout."""
        wide = drawn(morning, render_summary, WIDE).splitlines()
        narrow = drawn(morning, render_summary, width).splitlines()
        assert len(narrow) >= len(wide)
        for line in narrow:
            assert not line.rstrip().endswith(("·", "+", "—")), line


class TestTheNumbersSurviveTheSqueeze:
    def test_the_balance_is_still_there(self, morning):
        assert "+5.58" in drawn(morning, render_summary, PHONE)

    def test_so_are_the_punches_and_their_hours(self, morning):
        out = drawn(morning, render_summary, PHONE)
        assert "06:00–06:45" in out and "0.75" in out
        assert "08:20–…" in out

    def test_and_the_leave_time(self, morning):
        assert "16:35" in drawn(morning, render_summary, PHONE)

    def test_and_what_is_still_owed(self, morning):
        assert "4.58 to go" in drawn(morning, render_summary, PHONE)


class TestTheWideLayoutIsUntouched:
    def test_the_headline_stays_on_one_line(self, morning):
        line = next(l for l in drawn(morning, render_summary, WIDE).splitlines()
                    if "BANKED" in l)
        assert "worked" in line and "to 22/09" in line
