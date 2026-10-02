# -*- coding: utf-8 -*-
"""Parsing the attendance grid."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from hilan.parser import parse_month


@pytest.fixture(scope="session")
def sept(september_html):
    return parse_month(september_html)


@pytest.fixture(scope="session")
def aug(august_html):
    return parse_month(august_html)


def day(report, dom):
    return next(d for d in report.days if d.date.day == dom)


class TestHeader:
    def test_month_and_year_come_from_the_grid_id(self, sept):
        assert (sept.year, sept.month) == (2026, 9)

    def test_employee_is_read_from_the_strip(self, sept):
        assert sept.employee_number == "12345"
        assert "ישראל" in sept.employee_name

    def test_clock_sync_timestamp(self, sept):
        assert sept.clock_synced_at == datetime(2026, 9, 22, 16, 40)

    def test_every_day_of_the_month_is_present(self, sept, aug):
        assert len(sept.days) == 30
        assert len(aug.days) == 31

    def test_days_are_ordered(self, sept):
        assert [d.date.day for d in sept.days] == list(range(1, 31))


class TestPlainDay:
    def test_clock_and_report_agree(self, sept):
        d = day(sept, 1)
        assert d.clock[0].entry == time(7, 55)
        assert d.clock[0].exit == time(17, 5)
        assert d.report[0].entry == time(7, 55)
        assert d.report[0].exit == time(17, 5)
        assert d.report[0].total == timedelta(hours=9, minutes=10)
        assert d.report[0].standard == Decimal("9.00")
        assert d.report[0].symbol == "נוכחות"
        assert d.report[0].comment == ""

    def test_thursday_standard_is_short(self, sept):
        assert day(sept, 3).report[0].standard == Decimal("8.50")

    def test_comment_is_kept(self, sept):
        assert day(sept, 17).report[0].comment == "דיווח ידני"


class TestEmptyCells:
    def test_double_escaped_nbsp_reads_as_missing(self, sept):
        # Hilan writes an empty cell as the literal text "&nbsp;"; a segment
        # with nothing in it at all is dropped rather than kept as a blank.
        assert day(sept, 18).clock == []

    def test_a_half_filled_clock_segment_is_kept(self, aug):
        # Only the punch-out registered; the segment still matters.
        seg = day(aug, 25).clock[0]
        assert seg.entry is None and seg.exit is not None

    def test_a_day_with_no_report_has_no_segments(self, sept):
        assert day(sept, 18).report == []

    def test_blank_standard_is_none(self, sept):
        assert day(sept, 4).report[0].standard is None


class TestMultipleSegments:
    def test_two_report_segments_on_one_day(self, sept):
        d = day(sept, 7)
        assert len(d.report) == 2
        assert d.report[0].total == timedelta(hours=9, minutes=5)
        assert d.report[1].total == timedelta(minutes=45)
        assert d.report[1].symbol == "עבודה מהבית"

    def test_two_clock_segments_keep_partial_data(self, aug):
        d = day(aug, 25)
        assert len(d.clock) == 2
        assert d.clock[0].entry is None and d.clock[0].exit == time(12, 30)
        assert [s.total for s in d.report] == [
            timedelta(hours=4, minutes=30),
            timedelta(hours=4, minutes=40),
        ]


class TestSpecialDays:
    @pytest.mark.parametrize(
        "dom, label",
        [(11, "ערב חג"), (13, "חג"), (20, 'ערב יוה"כ'), (25, "ערב חג"), (27, "חול המועד")],
    )
    def test_holiday_labels(self, sept, dom, label):
        assert day(sept, dom).special == label

    def test_error_text_in_the_special_cell_is_not_a_holiday(self, sept):
        # 22/09 carries a validation message, not a holiday.
        d = day(sept, 22)
        assert d.special is None
        assert d.error == "קיים דיווח ללא פרויקט באותה השורה"

    def test_ordinary_day_has_neither(self, sept):
        d = day(sept, 1)
        assert d.special is None and d.error is None


class TestAbsences:
    def test_vacation_day_keeps_symbol_and_standard(self, sept):
        d = day(sept, 14)
        assert d.report[0].symbol == "חופשה"
        assert d.report[0].standard == Decimal("9.00")
        assert d.report[0].total is None

    def test_sick_days(self, aug):
        assert [day(aug, n).report[0].symbol for n in (10, 11, 12)] == ["מחלה"] * 3


class TestOpenDay:
    def test_entry_without_exit(self, sept):
        d = day(sept, 22)
        assert d.report[0].entry == time(8, 15)
        assert d.report[0].exit is None
        assert d.report[0].total is None

    def test_clock_out_missing_while_report_is_complete(self, sept):
        d = day(sept, 8)
        assert d.clock[0].entry == time(8, 40) and d.clock[0].exit is None
        assert d.report[0].exit == time(17, 40)


class TestCalendar:
    def test_error_day_is_flagged(self, sept):
        assert day(sept, 22).is_error_day is True

    def test_normal_day_is_not_flagged(self, sept):
        assert day(sept, 1).is_error_day is False

    def test_calendar_label_carries_the_day_total(self, sept):
        assert day(sept, 1).calendar_label == "9:10"

    def test_calendar_label_sums_segments(self, aug):
        # 7:30 + 2:00 reported, shown as one 9:30 total.
        assert day(aug, 13).calendar_label == "9:30"

    def test_absence_days_are_marked_in_the_calendar(self, aug):
        assert day(aug, 4).is_absence_day is True
        assert day(aug, 3).is_absence_day is False


class TestLegend:
    def test_totals(self, sept):
        t = sept.totals
        assert t.standard == Decimal("173.00")
        assert t.standard_to_date == Decimal("119.50")
        assert t.actual == Decimal("97.25")
        assert t.productive == Decimal("96.94")

    def test_vacation_balance_can_be_negative(self, sept):
        assert sept.totals.vacation_balance == Decimal("-12.50")

    def test_legend_belongs_to_the_current_payroll_month_only(self, sept, aug):
        # Browsing August still shows September's legend, so it must be
        # reported as not belonging to the parsed month.
        assert aug.totals_match_month is False
        assert sept.totals_match_month is True


class TestRobustness:
    def test_missing_grid_raises(self):
        with pytest.raises(ValueError):
            parse_month("<html><body>nothing here</body></html>")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_month("")


class TestMultipleGridsOnOnePage:
    """Hilan can render more than one month's grid into the same page."""

    def _two_grids(self, september_html, august_html, current_month):
        body = august_html.split("<body>")[1].split("</body>")[0]
        merged = september_html.replace("</body>", body + "</body>")
        return merged.replace(
            'name="ctl00$mp$currentMonth" value="01/09/2026"',
            f'name="ctl00$mp$currentMonth" value="{current_month}"',
        )

    def test_picks_the_month_the_page_says_it_shows(self, september_html, august_html):
        merged = self._two_grids(september_html, august_html, "01/08/2026")
        assert parse_month(merged).month == 8

    def test_picks_september_when_that_is_the_current_month(self, september_html, august_html):
        merged = self._two_grids(september_html, august_html, "01/09/2026")
        assert parse_month(merged).month == 9

    def test_refuses_to_guess(self, september_html, august_html):
        merged = self._two_grids(september_html, august_html, "01/07/2026")
        with pytest.raises(ValueError, match="does not identify"):
            parse_month(merged)


class TestRowsThatReportNothing:
    """A future day, or one not filled in, still carries Hilan's requirement."""

    def test_the_requirement_is_kept(self, sept):
        d = day(sept, 23)
        assert d.report == []
        assert d.idle_standards == [Decimal("9.00")]

    def test_a_row_with_nothing_at_all_keeps_nothing(self, sept):
        assert day(sept, 18).idle_standards == []

    def test_a_row_that_reports_something_is_a_segment_not_idle(self, sept):
        assert day(sept, 1).idle_standards == []


class TestHolidayCellsBelongToTheirGrid:
    def test_another_months_holiday_does_not_leak_in(self, september_html, august_html):
        """Rows are numbered from 0 in every grid; 11/09 is a holiday eve, 11/08 is not."""
        body = august_html.split("<body>")[1].split("</body>")[0]
        merged = september_html.replace("</body>", body + "</body>").replace(
            'name="ctl00$mp$currentMonth" value="01/09/2026"',
            'name="ctl00$mp$currentMonth" value="01/08/2026"',
        )
        august = parse_month(merged)
        assert august.month == 8
        assert day(august, 11).special is None


class TestValuesThatAreNotNumbers:
    @pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "abc", "9.0.0"])
    def test_a_requirement_cell_that_is_not_a_number_is_blank(self, value):
        from hilan.parser import _parse_decimal
        assert _parse_decimal(value) is None

    def test_a_trailing_full_stop_in_the_legend_is_not_part_of_the_number(self, september_html):
        page = september_html.replace("הינה: -12.50", "הינה: -12.50.")
        assert parse_month(page).totals.vacation_balance == Decimal("-12.50")


class TestRowsOfAnotherMonth:
    def test_a_row_labelled_with_another_month_is_skipped(self, september_html):
        page = september_html.replace('ov="30/09 יום', 'ov="30/08 יום')
        report = parse_month(page)
        assert 30 not in [d.date.day for d in report.days]
        assert len(report.days) == 29
