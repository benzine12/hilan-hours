# -*- coding: utf-8 -*-
"""Which session is running, what a day asks, and what the screen says about it.

Each case is a day of the made-up September (every other day worked exactly to
its requirement), checked in Python and, where it shows a number, in the widget.
"""
import io
import json
import re
from datetime import date, datetime, time, timedelta

import pytest
from rich.console import Console

from hilan.calc import IssueKind, hrs
from hilan.parser import parse_month
from hilan.render import render_summary, to_dict
from hilan.say import _hm
from test_edge_days import BLANK, GID, _row, at, needs_node, same_in_the_widget, september

EVENING = datetime(2026, 9, 22, 18, 0)


def screen(analysis) -> str:
    out = io.StringIO()
    render_summary(analysis, Console(file=out, width=140, color_system=None))
    return out.getvalue()


class TestAnOpenPunchIsRunningOnlyAfterEveryFinishedSession:
    CASES = {
        # A clock-in at 07:55 for a row reported as 08:00-17:00.
        "before-a-finished-row": {22: ([("07:55", BLANK), ("08:00", "17:00")], [_row("08:00", "17:00", "09:00")])},
        # A row whose exit reached only the clock, and a stray open punch inside it.
        "inside-a-clock-closed-row": {22: ([("08:00", "17:00"), ("08:40", BLANK)], [_row("08:00", "")])},
        # An exit forgotten at lunch, then an afternoon that was closed.
        "open-before-a-closed-row": {22: ([("08:00", BLANK), ("13:00", "17:00")],
                                          [_row("08:00", ""), _row("13:00", "17:00", "04:00")])},
    }

    @pytest.mark.parametrize("case", CASES)
    def test_nothing_runs_on(self, case):
        f = at(september(self.CASES[case]), EVENING).forecast
        assert f.entry is None, case
        assert f.worked_so_far <= timedelta(hours=9), hrs(f.worked_so_far)

    def test_a_later_clock_in_still_runs(self):
        day = {22: ([("08:00", "12:00"), ("13:00", BLANK)], [_row("08:00", "12:00", "04:00")])}
        f = at(september(day), datetime(2026, 9, 22, 15, 0)).forecast
        assert f.entry == time(13, 0) and f.worked_so_far == timedelta(hours=6)

    def test_of_two_open_rows_the_later_runs(self):
        day = {22: ([(BLANK, BLANK)], [_row("08:00", ""), _row("13:00", "")])}
        assert at(september(day), datetime(2026, 9, 22, 14, 0)).forecast.entry == time(13, 0)

    @needs_node
    @pytest.mark.parametrize("case", CASES)
    def test_the_widget_agrees(self, tmp_path, case):
        same_in_the_widget(tmp_path, september(self.CASES[case]), EVENING, datetime(2026, 9, 22, 9, 0))


class TestASuppliedEntry:
    @pytest.mark.parametrize("day,entry,worked", [
        ({22: ([(BLANK, BLANK)], [_row("10:00", "", "02:00")])}, time(8, 0), 2),           # a total, no exit
        ({22: ([("10:00", "12:00")], [_row("10:00", "")])}, time(8, 0), 2),                # exit in the clock only
        ({22: ([("10:00", "12:00")], [_row("10:00", "")])}, time(11, 0), 2),               # inside that session
    ], ids=["total-no-exit", "clock-exit", "inside"])
    def test_a_finished_session_wins(self, day, entry, worked):
        f = at(september(day), datetime(2026, 9, 22, 14, 0), assumed_entry=entry).forecast
        assert f.worked_so_far == timedelta(hours=worked) and not f.entry_assumed


class TestLastNightsShift:
    def page(self, today_rows, sync):
        return september({21: ([(BLANK, BLANK)], [_row("20:00", "")]), 22: today_rows}, sync=sync)

    def test_ends_once_today_has_begun(self):
        """Whoever clocked in this morning finished last night's shift first."""
        html = self.page(([("08:00", BLANK)], [_row("08:00", "")]), "22/09/2026 23:00")
        noon, after = at(html, datetime(2026, 9, 22, 12, 0)), at(html, datetime(2026, 9, 22, 12, 1))
        assert noon.settled_through == after.settled_through == date(2026, 9, 21)
        assert after.live_balance >= noon.live_balance

    def test_the_latest_open_entry_decides(self):
        html = september({22: ([(BLANK, BLANK)], [_row("08:00", ""), _row("22:00", "")]),
                          23: ([(BLANK, BLANK)], [_row(standard="9.00", symbol="")])},
                         sync="23/09/2026 00:30")
        assert at(html, datetime(2026, 9, 23, 1, 0)).settled_through == date(2026, 9, 21)

    @needs_node
    def test_the_widget_agrees(self, tmp_path):
        html = self.page(([("08:00", BLANK)], [_row("08:00", "")]), "22/09/2026 23:00")
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 22, 12, 0), datetime(2026, 9, 22, 12, 1))


class TestWhatADayAsks:
    def without_row(self, day_index):
        return re.sub(rf'<tr id="{GID}_row_{day_index}">.*?</tr>', "", september())

    def test_a_day_with_no_row_still_asks_its_hours_in_banked(self):
        a = at(self.without_row(14), EVENING)                 # 15/09, a Tuesday
        b = at(september(), EVENING)
        assert a.now.standard == b.now.standard
        assert any(i.kind is IssueKind.MISSING_DAY and i.date == date(2026, 9, 15) for i in a.issues)

    @needs_node
    def test_the_widget_agrees(self, tmp_path):
        same_in_the_widget(tmp_path, self.without_row(14), EVENING)

    # Two half days off on a day that asks 9.00 (an idle row states it).
    HALVES = {15: ([(BLANK, BLANK)], [_row("", "", BLANK, "4.50", "חופשה"),
                                      _row("", "", BLANK, "4.50", "מחלה"),
                                      _row("", "", BLANK, "9.00", "")])}

    def test_two_half_days_off_make_a_day(self):
        d = next(x for x in at(september(self.HALVES), EVENING).days if x.date == date(2026, 9, 15))
        assert d.standard == timedelta(hours=9) and d.credited == timedelta(hours=9)

    def test_rows_that_each_state_the_day_still_credit_it_once(self):
        day = {15: ([(BLANK, BLANK)], [_row("", "", BLANK, "4.50", "חופשה"), _row("", "", BLANK, "4.50", "מחלה")])}
        d = next(x for x in at(september(day), EVENING).days if x.date == date(2026, 9, 15))
        assert d.standard == d.credited == timedelta(hours=4, minutes=30)

    @needs_node
    def test_the_widget_adds_them_too(self, tmp_path):
        same_in_the_widget(tmp_path, september(self.HALVES), EVENING)


class TestClockOutsJudgedOneByOne:
    def kinds(self, day, dom=15):
        a = at(september({dom: day}), EVENING)
        return {i.kind for i in a.issues if i.date == date(2026, 9, dom)}

    def test_an_afternoon_clock_in_left_open_is_named(self):
        day = ([("08:00", "12:00"), ("14:00", BLANK)], [_row("08:00", "12:00", "04:00")])
        assert IssueKind.MISSING_CLOCK_OUT in self.kinds(day)

    def test_a_clock_in_before_a_row_filled_in_by_hand_is_not(self):
        day = ([("07:55", BLANK)], [_row("08:00", "17:00", "09:00")])
        assert IssueKind.MISSING_CLOCK_OUT not in self.kinds(day)


class TestTheTodayBlock:
    def test_a_clock_in_the_report_has_not_caught_up_with(self):
        day = {22: ([("09:00", BLANK)], [_row(symbol="")])}
        shown = screen(at(september(day), datetime(2026, 9, 22, 12, 0)))
        assert "09:00–…   3.00" in shown and "nothing recorded yet" not in shown
        assert "pass --in" not in shown

    def test_an_exit_only_the_clock_has(self):
        day = {22: ([("08:00", "12:00")], [_row("08:00", "")])}
        line = next(l for l in screen(at(september(day), datetime(2026, 9, 22, 15, 0))).splitlines() if "TODAY" in l)
        assert "08:00–12:00   4.00" in line and "running" not in line

    def test_a_month_worked_over(self):
        days = {d: ([("08:00", "20:00")], [_row("08:00", "20:00", "12:00")]) for d in range(1, 23)}
        line = next(l for l in screen(at(september(days), datetime(2026, 9, 30, 12, 0))).splitlines() if "MONTH" in l)
        assert re.search(r"over \d+\.\d\d$", line.strip()) and " of " not in line


class TestTheJson:
    def test_carries_the_live_balance_and_today(self):
        day = {22: ([("08:00", BLANK)], [_row("08:00", "")])}
        d = to_dict(at(september(day), datetime(2026, 9, 22, 12, 0)))
        assert d["live_balance"] == "-5.00"
        assert d["forecast"]["entry"] == "08:00" and d["forecast"]["leave_to_close"] == "17:00"
        assert d["forecast"]["worked_so_far"] == "4.00" and d["forecast"]["done"] is False
        json.dumps(d)                                          # all of it serialisable

    def test_null_without_a_row_for_today(self):
        html = re.sub(rf'<tr id="{GID}_row_21">.*?</tr>', "", september())
        assert to_dict(at(html, datetime(2026, 9, 22, 12, 0)))["forecast"] is None


class TestOneMinute:
    def test_is_said_as_one(self):
        assert _hm(timedelta(minutes=1)) == "1 minute"
        assert _hm(timedelta(minutes=2)) == "2 minutes"

    @needs_node
    def test_the_widget_says_it_the_same(self, tmp_path):
        # Today worked 9.00 and a minute: one minute up.
        day = {22: ([("08:00", "17:01")], [_row("08:00", "17:01", "09:01")])}
        same_in_the_widget(tmp_path, september(day), EVENING)


class TestAHolidayLabelAndAMessageInOneCell:
    """The special cell can hold both, as on a real page during חול המועד."""

    REAL = "חול המועד קיים דיווח ללא פרויקט באותה השורה"

    def page(self, text, dom=22):
        return september(html_edit=lambda h: h.replace(
            f'<tr id="{GID}_row_{dom - 1}">',
            f'<tr id="{GID}_row_{dom - 1}"><td id="{GID}_special_row_{dom - 1}" class="HolidayDay">{text}</td>', 1))

    def test_both_are_read(self):
        day = next(d for d in parse_month(self.page(self.REAL)).days if d.date == date(2026, 9, 22))
        assert day.special == "חול המועד"
        assert day.error == "קיים דיווח ללא פרויקט באותה השורה"

    def test_the_screen_shows_no_hebrew(self, monkeypatch):
        monkeypatch.delenv("HILAN_BIDI", raising=False)
        shown = screen(at(self.page(self.REAL), datetime(2026, 9, 22, 12, 0)))
        assert "a row is reported without a project" in shown
        assert not re.search(r"[֐-׿]", shown), shown

    def test_a_holiday_with_a_message_still_asks_nothing(self):
        a = at(self.page("חג קיים דיווח ללא פרויקט באותה השורה", dom=15), EVENING)
        assert next(d for d in a.days if d.date == date(2026, 9, 15)).standard == timedelta(0)

    @needs_node
    @pytest.mark.parametrize("text", [REAL, "חג קיים דיווח ללא פרויקט באותה השורה",
                                      "ערב חג קיים דיווח ללא פרויקט באותה השורה",
                                      "קיים דיווח ללא פרויקט באותה השורה חג"])
    def test_the_widget_agrees(self, tmp_path, text):
        same_in_the_widget(tmp_path, self.page(text, dom=15), EVENING)
