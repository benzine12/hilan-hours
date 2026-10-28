# -*- coding: utf-8 -*-
"""What sort of day each day is: the colour of its tile on the widget.

The widget computes the same thing in JavaScript; test_widget_parity.py holds
the two together on every hour of both made-up months and on random ones.
"""
from datetime import date, datetime

from conftest import build_html
from capture import CAPTURES
from hilan.calc import analyse
from hilan.parser import parse_month
from hilan.render import to_dict

SEPTEMBER = {
    1: "met", 2: "short", 3: "met", 4: "met", 5: "off", 6: "short", 7: "met",
    8: "met", 9: "met", 10: "met", 11: "off", 12: "off", 13: "holiday", 14: "leave",
    15: "met", 16: "met", 17: "met", 18: "off", 19: "off", 20: "met", 21: "holiday",
    22: "today", 23: "ahead", 24: "ahead", 25: "off", 26: "off",
    27: "ahead", 28: "ahead", 29: "ahead", 30: "ahead",
}


def kinds(when: datetime) -> dict[int, str]:
    a = analyse([parse_month(build_html(CAPTURES["2026-09"]))], today=when.date(), now=when)
    return {d.date.day: a.kind(d) for d in a.days}


def test_every_kind_on_the_made_up_month():
    assert kinds(datetime(2026, 9, 22, 14, 35)) == SEPTEMBER


def test_a_day_not_yet_settled_is_pending_not_short():
    """Just past midnight, yesterday's punches have not reached Hilan yet."""
    got = kinds(datetime(2026, 9, 23, 1, 0))
    assert got[22] == "pending" and got[23] == "today"


def test_a_weekday_eve_is_an_ordinary_day_and_a_friday_eve_a_day_off():
    got = kinds(datetime(2026, 9, 22, 14, 35))
    assert got[20] == "met"          # ערב יוה"כ on a Sunday: 4.00 asked, 4.00 worked
    assert got[11] == "off"          # ערב חג on a Friday asks nothing


def test_the_json_carries_it():
    a = analyse([parse_month(build_html(CAPTURES["2026-09"]))],
                today=date(2026, 9, 22), now=datetime(2026, 9, 22, 14, 35))
    assert {d["date"]: d["kind"] for d in to_dict(a)["days"]}["2026-09-14"] == "leave"
