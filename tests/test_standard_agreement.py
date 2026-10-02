# -*- coding: utf-8 -*-
"""Hilan states each day's requirement; our rule must not quietly disagree.

The rule reproduces Hilan's totals exactly for every day type in the captured
data. But it only knows the labels it
has met: a full holiday, an eve, חול המועד, a weekend. Meet a day type it has
never seen and it would fall through to "an ordinary 9.00" and be silently
wrong, which is the one failure mode this tool exists to prevent.

So where Hilan states the day's requirement, that is what is used, and any
disagreement with the rule is reported rather than absorbed.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from hilan.calc import IssueKind, analyse
from hilan.parser import parse_month, ReportSegment

TODAY = date(2026, 9, 23)


def set_hilan_standard(report, day, value):
    """Rewrite what Hilan says the day requires."""
    d = next(x for x in report.days if x.date == day)
    d.report = [
        ReportSegment(entry=s.entry, exit=s.exit, total=s.total,
                      standard=Decimal(value), comment=s.comment, symbol=s.symbol)
        for s in d.report
    ] or [ReportSegment(entry=None, exit=None, total=None,
                        standard=Decimal(value), comment="", symbol="נוכחות")]
    return report


@pytest.fixture
def report(september_html):
    from tests.test_forecast import september_on_the_23rd

    return september_on_the_23rd(september_html)


class TestTheRuleAgreesWithHilanToday:
    def test_no_disagreement_on_the_example_month(self, report):
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        assert [i for i in a.issues if i.kind is IssueKind.STANDARD_MISMATCH] == []


class TestAnUnknownDayTypeIsNotAbsorbed:
    def test_a_disagreement_is_reported(self, report):
        """Pretend Hilan calls 22/09 a 4.00 day; the rule says 9.00."""
        set_hilan_standard(report, date(2026, 9, 22), "4.00")
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        found = [i for i in a.issues if i.kind is IssueKind.STANDARD_MISMATCH]
        assert len(found) == 1
        assert found[0].date == date(2026, 9, 22)
        assert "4.00" in found[0].detail and "9.00" in found[0].detail

    def test_hilans_figure_is_the_one_used(self, report):
        set_hilan_standard(report, date(2026, 9, 22), "4.00")
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        day = next(d for d in a.days if d.date == date(2026, 9, 22))
        assert day.standard == timedelta(hours=4)

    def test_the_balance_follows_hilan_not_the_rule(self, report):
        before = analyse([report], today=TODAY,
                         now=datetime(2026, 9, 23, 12, 0)).now.balance
        set_hilan_standard(report, date(2026, 9, 22), "4.00")
        after = analyse([report], today=TODAY,
                        now=datetime(2026, 9, 23, 12, 0)).now.balance
        assert after - before == timedelta(hours=5)

    def test_it_is_worth_acting_on(self, report):
        set_hilan_standard(report, date(2026, 9, 22), "4.00")
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        assert all(i.actionable for i in a.issues
                   if i.kind is IssueKind.STANDARD_MISMATCH)


class TestWhereHilanSaysNothing:
    def test_the_rule_still_decides(self, report):
        """Weekends and holidays carry no rows, so nothing states a figure."""
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        saturday = next(d for d in a.days if d.date == date(2026, 9, 5))
        assert saturday.standard == timedelta(0)

    def test_a_holiday_with_no_rows_stays_zero(self, report):
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        holiday = next(d for d in a.days if d.date == date(2026, 9, 13))
        assert holiday.standard == timedelta(0)


class TestRowsThatDisagreeWithEachOther:
    def test_the_rule_wins_and_the_day_is_flagged(self, report):
        """Two rows claiming different requirements cannot both be the day's."""
        d = next(x for x in report.days if x.date == date(2026, 9, 22))
        d.report = [
            ReportSegment(entry=time(8, 15), exit=time(12, 0), total=timedelta(hours=3, minutes=45),
                          standard=Decimal("4.00"), comment="", symbol="נוכחות"),
            ReportSegment(entry=time(13, 0), exit=time(18, 5), total=timedelta(hours=5, minutes=5),
                          standard=Decimal("9.00"), comment="", symbol="נוכחות"),
        ]
        a = analyse([report], today=TODAY, now=datetime(2026, 9, 23, 12, 0))
        day = next(x for x in a.days if x.date == date(2026, 9, 22))
        assert day.standard == timedelta(hours=9)          # the rule
        flagged = [i for i in a.issues
                   if i.date == date(2026, 9, 22)
                   and i.kind is IssueKind.STANDARD_MISMATCH]
        assert len(flagged) == 1
        assert "different requirements" in flagged[0].detail
