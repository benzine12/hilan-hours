# -*- coding: utf-8 -*-
"""Days that are easy to get wrong, each checked in Python and in the widget.

A made-up September 2026 in which every day not under test is worked exactly
to its requirement, so the balance moves only for the day a test is about.
"""
import io
from datetime import date, datetime, time, timedelta

import pytest
from rich.console import Console

from conftest import build_html
from hilan.calc import IssueKind, analyse, hrs
from hilan.parser import parse_month
from hilan.render import render_brief, render_summary
from test_widget_parity import NODE, compare

BLANK = "&nbsp;"
WEEKDAY = {6: "א", 0: "ב", 1: "ג", 2: "ד", 3: "ה", 4: "ו", 5: "שבת"}
GID = "ctl00_mp_RG_Days_100012345_2026_09"
LEGEND = "<span>שעות בפועל 0.00</span>"
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _row(entry="", exit_="", total=BLANK, standard="", symbol="נוכחות"):
    return (entry, exit_, total, standard, "", symbol)


def september(days=None, *, sync="22/09/2026 16:40", before=(), html_edit=None):
    """September 2026, with `days` {day of month: (clock, report)} replacing the ordinary ones."""
    days = days or {}
    rows, calendar, n = [], [], 0
    for label, weekday in before:                       # rows of the month before
        rows.append((n, label, weekday, None, [(BLANK, BLANK)], [_row(symbol="")]))
        n += 1
    for dom in range(1, 31):
        day = date(2026, 9, dom)
        if dom in days:
            clock, report = days[dom]
        elif day.weekday() in (4, 5):                    # Friday, Saturday: nothing asked
            clock, report = [(BLANK, BLANK)], [_row(symbol="")]
        elif day.weekday() == 3:                         # Thursday: 8.50
            clock, report = [("08:00", "16:30")], [_row("08:00", "16:30", "08:30")]
        else:
            clock, report = [("08:00", "17:00")], [_row("08:00", "17:00", "09:00")]
        rows.append((n, f"{dom:02d}/09", WEEKDAY[day.weekday()], None, clock, report))
        calendar.append(((day - date(2000, 1, 1)).days, "cDIES CSD", ""))
        n += 1
    html = build_html({
        "gid": GID, "who": "ישראל ישראלי", "currentMonth": "01/09/2026",
        "sync": f"נתוני שעון מעודכנים לתאריך {sync}" if sync else "",
        "legend": LEGEND, "calendar": calendar, "rows": rows,
    })
    return html_edit(html) if html_edit else html


def at(html, when, **kwargs):
    return analyse([parse_month(html)], today=when.date(), now=when, **kwargs)


def same_in_the_widget(tmp_path, html, *moments):
    assert compare(tmp_path, [(html, list(moments))]) == []


class TestAShiftPastMidnight:
    """A night shift still running is not a day with nothing in it."""

    NIGHT = {22: ([("22:00", BLANK)], [_row("22:00", "")]), 23: ([(BLANK, BLANK)], [_row(standard="9.00", symbol="")])}

    def test_it_does_not_settle_the_night_before(self):
        a = at(september(self.NIGHT, sync="23/09/2026 00:30"), datetime(2026, 9, 23, 1, 0))
        assert a.settled_through == date(2026, 9, 21)
        assert a.now.balance == timedelta(0)

    def test_a_forgotten_clock_out_is_still_settled_and_named(self):
        """Seventeen hours on: no shift is that long, so the day is over and open."""
        a = at(september(self.NIGHT, sync="23/09/2026 14:30"), datetime(2026, 9, 23, 15, 0))
        assert a.settled_through == date(2026, 9, 22)
        assert any(i.kind is IssueKind.OPEN_DAY and i.date == date(2026, 9, 22) for i in a.issues)

    @needs_node
    def test_the_widget_agrees(self, tmp_path):
        same_in_the_widget(tmp_path, september(self.NIGHT, sync="23/09/2026 00:30"),
                           datetime(2026, 9, 23, 1, 0), datetime(2026, 9, 23, 13, 59),
                           datetime(2026, 9, 23, 14, 1), datetime(2026, 9, 22, 23, 0))


class TestAnExitThatReachedOnlyTheClock:
    """The punch out lands in the clock column before the reported row is closed."""

    DAY = {22: ([("08:00", "12:00")], [_row("08:00", "")])}

    def test_the_session_is_over(self):
        f = at(september(self.DAY), datetime(2026, 9, 22, 15, 0)).forecast
        assert f.worked_so_far == timedelta(hours=4)
        assert f.entry is None

    def test_a_later_clock_in_still_runs(self):
        day = {22: ([("08:00", "12:00"), ("13:00", BLANK)], [_row("08:00", "")])}
        f = at(september(day), datetime(2026, 9, 22, 15, 0)).forecast
        assert f.entry == time(13, 0)
        assert f.worked_so_far == timedelta(hours=6)

    @needs_node
    def test_the_widget_agrees(self, tmp_path):
        same_in_the_widget(tmp_path, september(self.DAY), datetime(2026, 9, 22, 15, 0),
                           datetime(2026, 9, 22, 10, 0))


class TestASuppliedEntry:
    def test_a_closed_row_covering_it_wins(self):
        day = {22: ([("08:30", "17:30")], [_row("08:30", "17:30", "09:00")])}
        f = at(september(day), datetime(2026, 9, 22, 18, 0), assumed_entry=time(8, 30)).forecast
        assert f.worked_so_far == timedelta(hours=9) and not f.entry_assumed

    def test_one_before_a_finished_row_is_ignored(self):
        day = {22: ([("08:30", "12:00")], [_row("08:30", "12:00", "03:30")])}
        f = at(september(day), datetime(2026, 9, 22, 14, 0), assumed_entry=time(8, 30)).forecast
        assert f.worked_so_far == timedelta(hours=3, minutes=30)

    def test_one_after_it_is_the_afternoon(self):
        day = {22: ([("08:30", "12:00")], [_row("08:30", "12:00", "03:30")])}
        f = at(september(day), datetime(2026, 9, 22, 14, 0), assumed_entry=time(13, 0)).forecast
        assert f.worked_so_far == timedelta(hours=4, minutes=30) and f.entry_assumed


class TestTheWidgetReadsEveryRow:
    @needs_node
    def test_when_the_grid_opens_with_the_month_before(self, tmp_path):
        html = september(before=[("30/08", "א"), ("31/08", "ב")])
        assert len(parse_month(html).days) == 30
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 30, 12, 0))


class TestAnExitWithNoEntry:
    def test_is_named(self):
        day = {15: ([(BLANK, BLANK)], [_row("", "17:00")])}
        a = at(september(day), datetime(2026, 9, 22, 12, 0))
        assert any(i.kind is IssueKind.OPEN_DAY and i.date == date(2026, 9, 15)
                   and "no entry" in i.detail for i in a.issues)


class TestWhatIsShownAsRunning:
    def screen(self, analysis, render):
        out = io.StringIO()
        render(analysis, Console(file=out, width=120, color_system=None))
        return out.getvalue()

    def test_only_the_open_session(self):
        day = {22: ([("08:00", "12:00"), ("13:00", BLANK)], [_row("08:00", "12:00")])}
        a = at(september(day), datetime(2026, 9, 22, 14, 0))
        assert "1.00 running" in self.screen(a, render_brief)

    def test_beside_leave_taken_today(self):
        day = {22: ([("12:00", BLANK)], [_row("08:00", "11:00", "03:00", "9.00", "חופשה")])}
        a = at(september(day), datetime(2026, 9, 22, 14, 0))
        assert "2.00 running" in self.screen(a, render_brief)

    def test_a_closed_row_without_a_total_is_worth_its_times(self):
        day = {22: ([("08:00", "12:00"), ("13:00", BLANK)], [_row("08:00", "12:00")])}
        a = at(september(day), datetime(2026, 9, 22, 14, 0))
        assert "08:00–12:00   4.00" in self.screen(a, render_summary)


class TestTheTimeToEndLevel:
    def test_is_shown_when_it_falls_before_midnight_and_closing_after(self):
        days = {1: ([("08:00", "20:30")], [_row("08:00", "20:30", "12:30")]),   # 3.50 up
                22: ([("16:00", BLANK)], [_row("16:00", "")])}
        a = at(september(days), datetime(2026, 9, 22, 17, 0))
        assert hrs(a.now.balance) == "3.50"
        shown = io.StringIO()
        render_summary(a, Console(file=shown, width=200, color_system=None))
        assert "leave 01:00 to close" in shown.getvalue()
        assert "21:30 to end level" in shown.getvalue()


@needs_node
class TestTheWidgetAgreesOnOddPages:
    def test_a_sync_date_after_markup(self, tmp_path):
        html = september(html_edit=lambda h: h.replace(
            "נתוני שעון מעודכנים לתאריך 22/09/2026 16:40",
            "<b>נתוני שעון מעודכנים לתאריך</b> 03/09/2026 10:00"))
        assert parse_month(html).clock_synced_at == datetime(2026, 9, 3, 10, 0)
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 8, 12, 0))

    def test_two_rows_for_one_date(self, tmp_path):
        html = september(before=[("15/09", "ג")])
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 22, 12, 0))

    @pytest.mark.parametrize("stated", ["8.325", "0.025-"])
    def test_a_requirement_on_a_half_minute(self, tmp_path, stated):
        """Half a minute rounds away from zero on both sides: 8.33, and -0.03."""
        days = {15: ([("08:00", "17:00")], [_row("08:00", "17:00", "09:00", stated)])}
        same_in_the_widget(tmp_path, september(days), datetime(2026, 9, 22, 12, 0))


@needs_node
class TestTheWidgetReadsMarkupLikeBeautifulSoup:
    def test_single_quoted_attributes(self, tmp_path):
        html = september(html_edit=lambda h: h.replace(f'id="{GID}_cellOf_ManualTotal_EmployeeReports_row_0_0" ov="09:00"',
                                                       f"id='{GID}_cellOf_ManualTotal_EmployeeReports_row_0_0' ov='11:00'"))
        assert "ov='11:00'" in html
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 22, 12, 0))

    def test_a_cell_inside_a_comment_or_a_script(self, tmp_path):
        # The same id as a real cell: a reader that sees it would take its value.
        ghost = f'<td id="{GID}_cellOf_ManualTotal_EmployeeReports_row_0_0" ov="05:00"></td>'
        html = september(html_edit=lambda h: h.replace(
            "</body>", f"<!-- {ghost} --><script>var t = '{ghost}';</script></body>"))
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 22, 12, 0))
