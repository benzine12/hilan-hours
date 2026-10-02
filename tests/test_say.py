# -*- coding: utf-8 -*-
"""One sentence, for Siri to read out and a notification to carry.

Neither can show a table, so it has to be a whole thought in plain words: no
columns, no abbreviations, and hours said the way a person says them rather
than as 7.79.
"""
from datetime import date, datetime, time, timedelta

import pytest

from hilan.calc import analyse
from hilan.parser import parse_month
from hilan.say import spoken

TODAY = date(2026, 9, 23)


@pytest.fixture
def morning(september_html):
    from tests.test_forecast import september_on_the_23rd, with_entry

    return analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                   today=TODAY, now=datetime(2026, 9, 23, 12, 0))


@pytest.fixture
def not_in_yet(september_html):
    from tests.test_forecast import september_on_the_23rd

    return analyse([september_on_the_23rd(september_html)], today=TODAY,
                   now=datetime(2026, 9, 23, 7, 0))


class TestItReadsAloud:
    """Siri says it, so "1 hours 3" is not a typo, it is heard."""

    @pytest.mark.parametrize(
        "delta, expected",
        [
            (timedelta(hours=1), "1 hour"),
            (timedelta(hours=1, minutes=3), "1 hour 3"),
            (timedelta(hours=2), "2 hours"),
            (timedelta(hours=2, minutes=30), "2 hours 30"),
            (timedelta(minutes=35), "35 minutes"),
        ],
    )
    def test_hours_agree_with_their_number(self, delta, expected):
        from hilan.say import _hm

        assert _hm(delta) == expected


class TestItIsASentence:
    def test_it_reads_as_prose(self, morning):
        said = spoken(morning)
        assert said[0].isupper() and said.rstrip().endswith(".")

    def test_no_table_characters_survive(self, morning):
        for ch in "·│─┌└⚠":
            assert ch not in spoken(morning)

    def test_hours_are_said_not_printed(self, morning):
        """4.42 is a column heading's idea of four hours twenty-five."""
        said = spoken(morning)
        assert "4.42" not in said
        assert "4 hours 25" in said

    def test_a_whole_number_of_hours_drops_the_minutes(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        a = analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                    today=TODAY, now=datetime(2026, 9, 23, 16, 35))
        assert "9 hours." in spoken(a) or "9 hours " in spoken(a)


class TestWhatItSays:
    def test_the_bank_comes_first(self, morning):
        """5.58 banked, 4.42 of a 9.00 day done: 1.00 if you stopped here."""
        assert spoken(morning).startswith("You are 1 hour up right now")

    def test_a_deficit_is_said_as_one(self, september_html):
        from tests.test_forecast import september_on_the_23rd

        report = september_on_the_23rd(september_html)
        for day in report.days:
            day.report = []
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        assert "down" in spoken(a)

    def test_today_and_the_leave_time_follow(self, morning):
        said = spoken(morning)
        assert "today" in said
        assert "16:35" in said

    def test_without_a_punch_it_says_so(self, not_in_yet):
        said = spoken(not_in_yet)
        assert "not clocked in" in said
        assert "leave" not in said

    def test_a_finished_day_is_not_told_to_leave(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        a = analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                    today=TODAY, now=datetime(2026, 9, 23, 19, 0))
        assert "done for today" in spoken(a)
        assert "You can leave" not in spoken(a)


class TestItStaysShort:
    def test_short_enough_for_a_notification(self, morning):
        assert len(spoken(morning)) <= 160, spoken(morning)

    def test_and_for_a_day_with_nothing_on_it(self, not_in_yet):
        assert len(spoken(not_in_yet)) <= 160


class TestItSpeaksTheLiveFigure:
    """Siri is asked in the middle of a day, not the morning after one."""

    @pytest.fixture
    def afternoon(self, september_html):
        from datetime import time as _t

        from hilan.parser import ReportSegment
        from tests.test_forecast import september_on_the_23rd

        report = september_on_the_23rd(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report = [
            ReportSegment(entry=_t(6, 0), exit=_t(6, 45), total=timedelta(minutes=45),
                          standard=None, comment="", symbol="נוכחות"),
            ReportSegment(entry=_t(8, 20), exit=_t(14, 5),
                          total=timedelta(hours=5, minutes=45),
                          standard=None, comment="", symbol="נוכחות"),
        ]
        return analyse([report], today=TODAY, now=datetime(2026, 9, 23, 17, 0))

    def test_the_number_is_the_one_that_counts_today(self, afternoon):
        said = spoken(afternoon)
        assert said.startswith("You are 3 hours 5 up")
        assert "5 hours 35" not in said

    def test_it_says_the_number_is_about_now(self, afternoon):
        assert "right now" in spoken(afternoon)

    def test_today_still_follows(self, afternoon):
        assert "6 hours 30 today" in spoken(afternoon)

    def test_still_short_enough_for_a_notification(self, afternoon):
        assert len(spoken(afternoon)) <= 160
