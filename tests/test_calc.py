# -*- coding: utf-8 -*-
"""Balances, problem days and the reconciliation against Hilan's own totals."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from hilan.calc import IssueKind, analyse
from hilan.parser import parse_month
from hilan.standard import hours

TODAY = date(2026, 9, 22)          # "today" in the made-up September


@pytest.fixture(scope="session")
def sept(september_html):
    return analyse([parse_month(september_html)], today=TODAY)


@pytest.fixture(scope="session")
def aug(august_html):
    return analyse([parse_month(august_html)], today=TODAY)


def td(h, m):
    return timedelta(hours=h, minutes=m)


class TestSettledThrough:
    def test_today_is_excluded_while_still_open(self, sept):
        # 22/09 is clocked in but not out, so the day cannot be counted yet.
        assert sept.settled_through == date(2026, 9, 21)

    def test_a_closed_row_today_does_not_settle_the_day(self, september_html):
        """A finished row is not a finished day.

        Counting today as soon as it holds a closed דיווח row would settle a day
        with an early row of 06:00-06:45 at breakfast, drawing its whole
        9.00 requirement for a day not yet worked. A day is settled when Hilan's
        clock sync has moved past it, and by nothing else.
        """
        report = parse_month(september_html)
        closed = next(d for d in report.days if d.date.day == 17)
        assert any(s.entry and s.exit for s in closed.report)
        report.clock_synced_at = report.clock_synced_at.replace(day=17, hour=18)
        assert analyse([report], today=closed.date).settled_through == date(2026, 9, 16)

    def test_past_month_is_settled_to_its_last_day(self, aug):
        assert aug.settled_through == date(2026, 8, 31)


class TestWorked:
    def test_september_matches_the_sum_of_daily_totals(self, sept):
        assert sept.now.worked == td(106, 15)

    def test_august_total(self, aug):
        assert aug.now.worked == td(154, 45)

    def test_work_from_home_counts_as_worked(self, sept):
        d = next(d for d in sept.days if d.date.day == 7)
        assert d.worked == td(9, 50)          # 9:05 on site + 0:45 from home

    def test_an_open_segment_contributes_nothing(self, sept):
        d = next(d for d in sept.days if d.date.day == 22)
        assert d.worked == timedelta(0)


class TestCredit:
    def test_vacation_credits_the_day_standard(self, sept):
        d = next(d for d in sept.days if d.date.day == 14)
        assert d.credited == td(9, 0)         # a full day of vacation

    def test_sick_days_are_credited(self, aug):
        assert aug.now.credited == td(44, 30)  # 8:30 + 3x9:00 + 9:00

    def test_absences_can_be_switched_off(self, september_html):
        strict = analyse([parse_month(september_html)], today=TODAY, credit_absences=False)
        assert strict.now.credited == timedelta(0)
        assert strict.now.balance == sept_balance_without_credit()


def sept_balance_without_credit():
    return td(106, 15) - td(110, 30)


class TestBalanceNow:
    def test_september_balance(self, sept):
        assert sept.now.standard == td(110, 30)
        assert sept.now.balance == td(4, 45)

    def test_august_balance(self, aug):
        assert aug.now.standard == td(196, 0)
        assert aug.now.balance == td(3, 15)


class TestWeek:
    def test_week_runs_sunday_to_saturday(self, sept):
        assert sept.week_start == date(2026, 9, 20)
        assert sept.week_end == date(2026, 9, 26)

    def test_current_week_is_square(self, sept):
        # 20/09 is Yom Kippur eve, 4:00 required and 4:00 worked; 21/09 a holiday.
        assert sept.week.standard == td(4, 0)
        assert sept.week.worked == td(4, 0)
        assert sept.week.balance == timedelta(0)

    def test_week_is_clipped_to_settled_days(self, sept):
        assert all(d <= sept.settled_through for d in sept.week_days)


class TestMonth:
    def test_full_month_standard(self, sept):
        assert hours(sept.month.standard) == Decimal("173.00")

    def test_remaining_hours(self, sept):
        assert sept.month.remaining == td(57, 45)

    def test_august_month_standard(self, aug):
        assert hours(aug.month.standard) == Decimal("196.00")


class TestReconciliation:
    def test_gap_against_hilan_actual(self, sept):
        assert sept.reconciliation is not None
        assert sept.reconciliation.gap == td(9, 0)

    def test_gap_is_attributed_to_the_unapproved_day(self, sept):
        culprits = [d.date.day for d in sept.reconciliation.candidates]
        assert culprits == [8]

    def test_no_reconciliation_for_a_past_month(self, aug):
        # The legend always shows the current payroll month, so comparing it
        # against August would be comparing against the wrong month entirely.
        assert aug.reconciliation is None


class TestIssues:
    def kinds(self, analysis, dom):
        return {i.kind for i in analysis.issues if i.date.day == dom}

    def test_a_covered_missing_clock_out_is_not_flagged(self, sept):
        """08/09 has no turnstile exit but a דיווח row that closes the day."""
        assert IssueKind.MISSING_CLOCK_OUT not in self.kinds(sept, 8)

    def test_todays_hilan_error_is_pending_not_actionable(self, sept):
        # "קיים דיווח ללא פרויקט" shows up before the punch has synced and
        # clears itself, so it must not be presented as something to fix.
        issues = [i for i in sept.issues if i.date.day == 22]
        assert issues
        assert all(i.kind is IssueKind.PENDING_SYNC for i in issues)
        assert all(not i.actionable for i in issues)

    def test_an_uncovered_missing_clock_out_is_actionable(self, september_html):
        report = parse_month(september_html)
        next(d for d in report.days if d.date.day == 8).report = []
        a = analyse([report], today=TODAY)
        issue = next(i for i in a.issues if i.kind is IssueKind.MISSING_CLOCK_OUT)
        assert issue.actionable

    def test_clean_days_produce_no_issues(self, sept):
        assert self.kinds(sept, 1) == set()

    def test_a_skipped_workday_is_reported(self, september_html):
        report = parse_month(september_html)
        for d in report.days:
            if d.date.day == 15:
                d.report = []
                d.clock = []
        analysis = analyse([report], today=TODAY)
        assert IssueKind.MISSING_DAY in {i.kind for i in analysis.issues if i.date.day == 15}

    def test_report_well_below_the_clock_is_flagged(self, september_html):
        report = parse_month(september_html)
        for d in report.days:
            if d.date.day == 1:
                d.report = [d.report[0].__class__(
                    entry=d.report[0].entry, exit=d.report[0].exit,
                    total=timedelta(hours=6), standard=d.report[0].standard,
                    comment="", symbol="נוכחות")]
        analysis = analyse([report], today=TODAY)
        assert IssueKind.CLOCK_MISMATCH in {i.kind for i in analysis.issues if i.date.day == 1}

    def test_extra_hours_beyond_the_clock_are_not_flagged(self, sept):
        # 07/09 has 45 minutes of evening work from home with no punch.
        assert IssueKind.CLOCK_MISMATCH not in self.kinds(sept, 7)


class TestCrossMonthWeek:
    def test_week_spanning_two_months_uses_both(self, september_html, august_html):
        # 30/08 is a Sunday, so that week reaches into September.
        analysis = analyse(
            [parse_month(august_html), parse_month(september_html)],
            today=date(2026, 9, 2),
        )
        assert analysis.week_start == date(2026, 8, 30)
        assert analysis.week_end == date(2026, 9, 5)
        # 30/08 11.50 + 31/08 7.50 from August's page, 01/09 9.17 from September's.
        assert analysis.week.worked == timedelta(hours=28, minutes=10)


class TestPartialAbsence:
    """Leave fills the gap left by work — it never stacks on top of a full day."""

    def _with_half_day(self, september_html, worked_total):
        from hilan.parser import ReportSegment
        report = parse_month(september_html)
        target = next(d for d in report.days if d.date.day == 15)   # 9h standard
        target.report = [
            ReportSegment(entry=None, exit=None, total=worked_total, standard=None,
                          comment="", symbol="נוכחות"),
            ReportSegment(entry=None, exit=None, total=None, standard=Decimal("9.00"),
                          comment="", symbol="חופשה"),
        ]
        return next(d for d in analyse([report], today=TODAY).days if d.date.day == 15)

    def test_half_day_of_leave_tops_the_day_up_to_the_standard(self, september_html):
        d = self._with_half_day(september_html, td(4, 30))
        assert d.worked == td(4, 30)
        assert d.credited == td(4, 30)
        assert d.balance == timedelta(0)

    def test_leave_never_pushes_a_full_day_into_surplus(self, september_html):
        d = self._with_half_day(september_html, td(9, 0))
        assert d.credited == timedelta(0)
        assert d.balance == timedelta(0)

    def test_overtime_plus_leave_still_counts_the_overtime(self, september_html):
        d = self._with_half_day(september_html, td(10, 0))
        assert d.credited == timedelta(0)
        assert d.balance == td(1, 0)


class TestSyncLagCapsTheCut:
    """Hilan tells us how far its clock data reaches; ignoring that invents work.

    The punch shows up a couple of hours after you leave, so on any morning the
    sync marker still points at yesterday afternoon. Yesterday's missing exit is
    then not a day to go and fix — it is a day Hilan has not finished writing.
    """

    def test_a_day_the_sync_has_not_reached_is_not_settled(self, september_html):
        # Captured on 22/09 with the marker at 22/09 16:40 and no exit that day.
        # Come the 23rd the marker still says 22/09 16:40: the exit is missing
        # from the data, not from reality.
        a = analyse([parse_month(september_html)], today=date(2026, 9, 23))
        assert a.settled_through == date(2026, 9, 21)

    def test_such_a_day_is_reported_as_pending_not_actionable(self, september_html):
        a = analyse([parse_month(september_html)], today=date(2026, 9, 23))
        for issue in a.issues:
            if issue.date == date(2026, 9, 22):
                assert not issue.actionable, f"22/09 flagged as actionable: {issue.kind}"

    def test_the_week_balance_does_not_invent_a_deficit(self, september_html):
        """-9:00 for a week whose hours simply have not landed would be a lie."""
        a = analyse([parse_month(september_html)], today=date(2026, 9, 23))
        assert a.week.balance == timedelta(0)

    def test_a_sync_past_the_day_lets_it_settle(self, september_html):
        """Once the marker moves beyond the day, the missing exit is real."""
        report = parse_month(september_html)
        report.clock_synced_at = report.clock_synced_at.replace(day=24)
        a = analyse([report], today=date(2026, 9, 24))
        assert a.settled_through == date(2026, 9, 23)
        kinds = {i.kind for i in a.issues if i.date == date(2026, 9, 22) and i.actionable}
        assert kinds, "22/09 should be actionable once the sync has passed it"

    def test_without_a_marker_the_cut_is_the_day_before_today(self, september_html):
        report = parse_month(september_html)
        report.clock_synced_at = None
        a = analyse([report], today=date(2026, 9, 23))
        assert a.settled_through == date(2026, 9, 22)


class TestTodayIsNeverSettled:
    """A closed early row must not settle today and invent a deficit."""

    def test_an_early_session_does_not_close_the_day(self, september_html):
        report = parse_month(september_html)
        # A closed row at 06:00-06:45 and the clock synced at 07:00 the same
        # morning.
        report.clock_synced_at = report.clock_synced_at.replace(day=23, hour=7, minute=0)
        a = analyse([report], today=date(2026, 9, 23))
        assert a.settled_through == date(2026, 9, 22)

    def test_todays_requirement_is_not_counted(self, september_html):
        report = parse_month(september_html)
        report.clock_synced_at = report.clock_synced_at.replace(day=23, hour=7, minute=0)
        a = analyse([report], today=date(2026, 9, 23))
        assert date(2026, 9, 23) not in a.week_days

    def test_without_a_marker_today_still_does_not_count(self, september_html):
        report = parse_month(september_html)
        report.clock_synced_at = None
        a = analyse([report], today=date(2026, 9, 17))
        assert a.settled_through == date(2026, 9, 16)


class TestDurationsReadAsDecimalHours:
    """Hilan writes durations as decimal hours (תקן 173.00, בפועל 97.25).

    Printing h:mm beside those invites exactly one mistake: reading 106:15 as
    106.15. One notation everywhere, and it is Hilan's.
    """

    @pytest.mark.parametrize(
        "delta, expected",
        [
            (td(106, 15), "106.25"),
            (td(119, 30), "119.50"),
            (td(9, 10), "9.17"),
            (td(8, 30), "8.50"),
            (td(4, 0), "4.00"),
            (td(0, 0), "0.00"),
            (td(0, 45), "0.75"),
            (-td(1, 43), "-1.72"),
            (-td(9, 0), "-9.00"),
        ],
    )
    def test_formatting(self, delta, expected):
        from hilan.calc import hrs

        assert hrs(delta) == expected

    def test_it_matches_hilans_own_column(self, sept):
        """Our requirement to date has to print identically to תקן עד היום."""
        from hilan.calc import hrs

        assert hrs(sept.now.standard) == "110.50"

    def test_minutes_never_leak_into_the_output(self, sept):
        from hilan.calc import hrs

        for value in (sept.now.worked, sept.now.standard, sept.now.balance):
            assert ":" not in hrs(value)


class TestRoundingOfTotals:
    """A total is the rounded exact sum, never the sum of rounded days.

    Showing each day to two places and adding the column can land a cent away
    from the total. The total has to stay the honest one, because that is the
    number Hilan's בפועל is compared against.
    """

    def test_the_total_is_not_the_sum_of_the_printed_days(self):
        from hilan.calc import hrs

        days = [timedelta(hours=1, minutes=1)] * 3      # 1.0166.. each
        assert hrs(days[0]) == "1.02"                   # printed per day
        assert hrs(sum(days, timedelta(0))) == "3.05"   # printed total
        # Adding the printed column would give 3.06; the total does not lie
        # to make the column add up.
        assert Decimal(hrs(days[0])) * 3 != Decimal(hrs(sum(days, timedelta(0))))

    def test_a_day_column_may_differ_from_its_total_by_a_cent(self, sept):
        from hilan.calc import hrs

        column = sum(Decimal(hrs(d.worked)) for d in sept.days)
        total = Decimal(hrs(sept.month.worked))
        assert abs(column - total) <= Decimal("0.01")


class TestAMissingPunchThatHasBeenDealtWith:
    """Filling the exit in by hand is the normal fix, not an outstanding problem.

    08/09 shows the turnstile holding an entry with no exit and a דיווח row of
    08:40-17:40 filled in by hand. Hilan marks no error, its totals include the
    day, and there is nothing to go and do. Reporting it would send the reader
    to look for something that is not there.
    """

    def day(self, html, when):
        report = parse_month(html)
        return next(d for d in report.days if d.date == when), report

    def test_a_covered_missing_punch_is_not_reported(self, september_html):
        a = analyse([parse_month(september_html)], today=TODAY)
        assert [i for i in a.issues
                if i.date == date(2026, 9, 8)
                and i.kind is IssueKind.MISSING_CLOCK_OUT] == []

    def test_an_uncovered_one_still_is(self, september_html):
        """Take the reported row away and the missing punch means something."""
        rec, report = self.day(september_html, date(2026, 9, 8))
        rec.report = []
        a = analyse([report], today=TODAY)
        assert [i.kind for i in a.issues if i.date == date(2026, 9, 8)] == [
            IssueKind.MISSING_CLOCK_OUT
        ]

    def test_an_open_reported_row_does_not_cover_it_either(self, september_html):
        from hilan.parser import ReportSegment
        from datetime import time as _t

        rec, report = self.day(september_html, date(2026, 9, 8))
        rec.report = [ReportSegment(entry=_t(8, 40), exit=None, total=None,
                                    standard=None, comment="", symbol="נוכחות")]
        a = analyse([report], today=TODAY)
        kinds = {i.kind for i in a.issues if i.date == date(2026, 9, 8)}
        assert IssueKind.MISSING_CLOCK_OUT in kinds


class TestTheSummaryLagsTheRows:
    """Hilan's own summary is a snapshot, and today is still moving.

    Say the day rows hold 06:00-06:45 and 08:20-14:05, and בפועל has taken in
    the early session but not the later one. Our live total is then 5.75
    above it — exactly the punch the summary has not reached — which is no
    discrepancy at all.

    A difference no larger than today's own hours is evidence of nothing, since
    those are the hours the summary may not have seen yet.
    """

    def report_with(self, html, punches, actual):
        from decimal import Decimal
        from hilan.parser import HilanTotals, ReportSegment

        report = parse_month(html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report = [
            ReportSegment(entry=a, exit=b, total=c, standard=Decimal("9.00"),
                          comment="", symbol="נוכחות")
            for a, b, c in punches
        ]
        report.clock_synced_at = report.clock_synced_at.replace(day=23, hour=17)
        before = report.totals
        report.totals = HilanTotals(
            standard=before.standard, standard_to_date=before.standard_to_date,
            actual=Decimal(actual), productive=Decimal(actual),
            vacation_balance=before.vacation_balance,
        )
        return report

    def test_a_gap_no_bigger_than_today_is_not_reported(self, september_html):
        from datetime import time as _t

        report = self.report_with(
            september_html,
            [(_t(6, 0), _t(6, 45), timedelta(minutes=45)),
             (_t(8, 20), _t(14, 5), timedelta(hours=5, minutes=45))],
            "107.00",
        )
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 17, 0))
        assert a.reconciliation is None

    def test_a_gap_larger_than_today_still_is(self, september_html):
        """Nine hours missing is the case this tool was built for."""
        from datetime import time as _t

        # Ours comes to 112.00 with today's 5.75 in it, so 103.00 leaves a gap
        # of 9.00 — well past anything today could explain.
        report = self.report_with(
            september_html,
            [(_t(8, 20), _t(14, 5), timedelta(hours=5, minutes=45))],
            "103.00",
        )
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 17, 0))
        assert a.reconciliation is not None
        assert a.reconciliation.gap > timedelta(hours=5, minutes=45)

    def test_a_day_with_no_hours_leaves_the_comparison_alone(self, september_html):
        report = self.report_with(september_html, [], "97.25")
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 17, 0))
        assert a.reconciliation is not None


class TestARequirementOnARowThatReportsNothing:
    """Hilan states the requirement on a day's row even when nothing is reported."""

    def _idle(self, september_html, dom, value):
        report = parse_month(september_html)
        d = next(x for x in report.days if x.date.day == dom)
        d.report, d.clock, d.idle_standards = [], [], [Decimal(value)]
        return analyse([report], today=TODAY), d.date

    def test_a_day_off_that_hilan_declares_asks_nothing(self, september_html):
        a, when = self._idle(september_html, 15, "0.00")
        assert next(x for x in a.days if x.date == when).standard == timedelta(0)

    def test_so_it_is_not_a_missing_day(self, september_html):
        a, when = self._idle(september_html, 15, "0.00")
        assert IssueKind.MISSING_DAY not in {i.kind for i in a.issues if i.date == when}

    def test_a_requirement_the_rule_would_not_set_is_reported(self, september_html):
        a, when = self._idle(september_html, 15, "4.00")
        assert next(x for x in a.days if x.date == when).standard == td(4, 0)
        assert IssueKind.STANDARD_MISMATCH in {i.kind for i in a.issues if i.date == when}

    def test_it_joins_the_days_other_rows(self, september_html):
        """Two different requirements on one day cannot both be it: the rule decides."""
        report = parse_month(september_html)
        d = next(x for x in report.days if x.date.day == 15)
        d.idle_standards = [Decimal("4.00")]          # beside its worked row's 9.00
        a = analyse([report], today=TODAY)
        calc = next(x for x in a.days if x.date == d.date)
        assert calc.stated_conflict and calc.standard == td(9, 0)


class TestTheMonthAsksWhatHilanSays:
    def test_stated_requirements_make_up_the_month(self, september_html):
        report = parse_month(september_html)
        first = next(d for d in report.days if d.date.day == 1)
        first.report = [first.report[0].__class__(
            entry=first.report[0].entry, exit=first.report[0].exit,
            total=first.report[0].total, standard=Decimal("8.00"),
            comment="", symbol="נוכחות")]
        a = analyse([report], today=TODAY)
        assert hours(a.month.standard) == Decimal("172.00")       # 173.00 less one hour

    def test_without_rows_the_rule_fills_in(self, september_html):
        report = parse_month(september_html)
        report.days = [d for d in report.days if d.date.day != 15]   # a Tuesday, 9.00
        assert hours(analyse([report], today=TODAY).month.standard) == Decimal("173.00")


class TestDayTypes:
    """Which rows count as work, which as time off, and what is said about the rest."""

    def _day(self, september_html, *segments, dom=15):
        from hilan.parser import ReportSegment
        report = parse_month(september_html)
        d = next(x for x in report.days if x.date.day == dom)
        d.report = [ReportSegment(entry=e, exit=x, total=t, standard=Decimal("9.00"),
                                  comment="", symbol=s) for e, x, t, s in segments]
        d.clock = []
        a = analyse([report], today=TODAY)
        return next(x for x in a.days if x.date == d.date), a

    def test_nichach_is_work(self, september_html):
        from datetime import time as _t
        day, _ = self._day(september_html, (_t(8, 0), _t(11, 0), None, "נכח"))
        assert day.worked == td(3, 0) and day.credited == timedelta(0)

    def test_an_unknown_type_with_hours_is_not_a_free_day(self, september_html):
        """Three hours of an unknown type on a 9.00 day leave the day six short."""
        from datetime import time as _t
        day, _ = self._day(september_html, (_t(8, 0), _t(11, 0), None, "השתלמות"))
        assert day.credited == td(3, 0)
        assert day.balance == -td(6, 0)

    def test_and_it_is_said_out_loud(self, september_html):
        from datetime import time as _t
        day, a = self._day(september_html, (_t(8, 0), _t(11, 0), None, "השתלמות"))
        found = [i for i in a.issues if i.date == day.date and i.kind is IssueKind.UNKNOWN_SYMBOL]
        assert len(found) == 1 and "השתלמות" in found[0].detail and found[0].actionable

    def test_known_types_raise_nothing(self, september_html):
        day, a = self._day(september_html, (None, None, None, "חופשה"))
        assert not [i for i in a.issues if i.kind is IssueKind.UNKNOWN_SYMBOL]

    def test_leave_with_its_hours_is_worth_those_hours(self, september_html):
        from datetime import time as _t
        day, _ = self._day(september_html,
                           (_t(8, 0), _t(12, 0), None, "נוכחות"),
                           (_t(12, 0), _t(14, 0), None, "מחלה"))
        assert day.worked == td(4, 0) and day.credited == td(2, 0)
        assert day.balance == -td(3, 0)

    def test_leave_without_hours_still_covers_the_day(self, september_html):
        day, _ = self._day(september_html, (None, None, None, "מחלה"))
        assert day.credited == td(9, 0) and day.balance == timedelta(0)


class TestRequirementsInWholeMinutes:
    def test_a_third_of_an_hour_is_twenty_minutes(self):
        from hilan.calc import _minutes
        assert _minutes(Decimal("8.33")) == td(8, 20)
        assert _minutes(Decimal("7.42")) == td(7, 25)
        assert _minutes(Decimal("9.00")) == td(9, 0)
