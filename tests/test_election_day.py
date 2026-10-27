# -*- coding: utf-8 -*-
"""Election day: a paid day off by law, which Hilan marks in the day's special cell.

The label is not ``חג``, so before it was known the parser took it for a
validation message: the day asked a full 9.00, today's forecast said 9.00 to
go, and once settled the day showed as an error with nothing reported.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from conftest import build_html
from hilan.calc import IssueKind, analyse
from hilan.parser import _classify_special, parse_month
from hilan.standard import daily_standard, hours
from hilan.text import english
from test_widget_parity import NODE, compare

BLANK = "&nbsp;"
WEEKDAY = {6: "א", 0: "ב", 1: "ג", 2: "ד", 3: "ה", 4: "ו", 5: "שבת"}
GID = "ctl00_mp_RG_Days_100012345_2026_10"
ELECTION = date(2026, 10, 27)                    # a Tuesday
LABELS = ["יום בחירות", "בחירות", "יום הבחירות", "בחירות לכנסת"]
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _row(entry="", exit_="", total=BLANK, standard="", symbol="נוכחות"):
    return (entry, exit_, total, standard, "", symbol)


def october(label="יום בחירות", election=None, sync="27/10/2026 10:15"):
    """October 2026, every working day worked to its requirement but election day."""
    rows, calendar = [], []
    for n, dom in enumerate(range(1, 32)):
        day = date(2026, 10, dom)
        special = None
        if day == ELECTION:
            special = label
            clock, report = election or ([(BLANK, BLANK)], [_row(symbol="")])
        elif day.weekday() in (4, 5):
            clock, report = [(BLANK, BLANK)], [_row(symbol="")]
        elif day.weekday() == 3:
            clock, report = [("08:00", "16:30")], [_row("08:00", "16:30", "08:30")]
        else:
            clock, report = [("08:00", "17:00")], [_row("08:00", "17:00", "09:00")]
        rows.append((n, f"{dom:02d}/10", WEEKDAY[day.weekday()], special, clock, report))
        cls = "cHD CSD" if special else "cDIES CSD"
        calendar.append(((day - date(2000, 1, 1)).days, cls, special or ""))
    return build_html({
        "gid": GID, "who": "ישראל ישראלי", "currentMonth": "01/10/2026",
        "sync": f"נתוני שעון מעודכנים לתאריך {sync}",
        "legend": "<span>שעות בפועל 0.00</span>", "calendar": calendar, "rows": rows,
    })


def at(html, when):
    return analyse([parse_month(html)], today=when.date(), now=when)


class TestTheLabel:
    @pytest.mark.parametrize("label", LABELS)
    def test_it_is_a_label_not_a_message(self, label):
        assert _classify_special(label) == (label, None)

    def test_a_message_beside_it_stays_a_message(self):
        message = "קיים דיווח ללא פרויקט באותה השורה"
        assert _classify_special(f"יום בחירות {message}") == ("יום בחירות", message)

    @pytest.mark.parametrize("label", LABELS)
    def test_it_asks_nothing(self, label):
        assert daily_standard(ELECTION, label) == timedelta(0)

    def test_it_reads_in_english(self):
        assert english("יום בחירות") == "election day"


class TestTheDay:
    def test_today_asks_nothing(self):
        a = at(october(), datetime(2026, 10, 27, 12, 0))
        assert a.forecast.required == timedelta(0)
        assert a.forecast.done_for_today
        assert a.live_balance == a.now.balance == timedelta(0)

    def test_today_is_not_waiting_for_a_sync(self):
        a = at(october(), datetime(2026, 10, 27, 12, 0))
        assert [i for i in a.issues if i.date == ELECTION] == []

    def test_once_settled_it_is_neither_missing_nor_an_error(self):
        a = at(october(sync="28/10/2026 10:15"), datetime(2026, 10, 28, 12, 0))
        assert a.settled_through == ELECTION
        assert a.now.balance == timedelta(0)
        assert [i for i in a.issues if i.date == ELECTION] == []

    def test_the_month_does_not_ask_for_it(self):
        # 16 days of 9.00 and 5 Thursdays of 8.50 make 186.50; the 27th is not among them.
        a = at(october(), datetime(2026, 10, 27, 12, 0))
        assert hours(a.month.standard) == Decimal("186.50") - Decimal("9.00")

    def test_hours_worked_on_it_are_a_surplus(self):
        worked = ([("08:00", "12:00")], [_row("08:00", "12:00", "04:00")])
        a = at(october(election=worked, sync="28/10/2026 10:15"),
               datetime(2026, 10, 28, 12, 0))
        assert a.now.balance == timedelta(hours=4)

    def test_hilans_own_figure_still_decides(self):
        """Where Hilan states a requirement for the day, it wins, and is said aloud."""
        stated = ([(BLANK, BLANK)], [_row(standard="9.00", symbol="")])
        a = at(october(election=stated, sync="28/10/2026 10:15"),
               datetime(2026, 10, 28, 12, 0))
        assert any(i.kind is IssueKind.STANDARD_MISMATCH and i.date == ELECTION
                   for i in a.issues)


@needs_node
class TestTheWidgetAgrees:
    @pytest.mark.parametrize("label", LABELS)
    def test_on_the_day_and_after(self, tmp_path, label):
        moments = [datetime(2026, 10, 27, h, 0) for h in (8, 12, 18)]
        moments += [datetime(2026, 10, 28, 12, 0)]
        assert compare(tmp_path, [(october(label), moments)]) == []
