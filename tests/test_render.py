# -*- coding: utf-8 -*-
"""What the user actually reads."""
from datetime import date, datetime, timedelta

import pytest
from rich.console import Console

from hilan.calc import analyse
from hilan.parser import parse_month
from hilan.render import render_days, render_summary, to_dict

TODAY = date(2026, 9, 22)


def text_of(fn, analysis) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    fn(analysis, console)
    return console.export_text()


@pytest.fixture(scope="session")
def sept(september_html):
    return analyse([parse_month(september_html)], today=TODAY)


@pytest.fixture(scope="session")
def aug(august_html):
    return analyse([parse_month(august_html)], today=TODAY)


class TestSummary:
    def test_headline_balance(self, sept):
        assert "+4.75" in text_of(render_summary, sept)

    def test_month_line_present_for_the_current_month(self, sept):
        assert "MONTH" in text_of(render_summary, sept)

    def test_month_line_present_for_a_past_month_too(self, aug):
        assert "MONTH" in text_of(render_summary, aug)

    def test_remaining_hours_shown(self, sept):
        assert "57.75" in text_of(render_summary, sept)

    def test_reconciliation_names_the_day(self, sept):
        out = text_of(render_summary, sept)
        assert "-9.00" in out and "08/09" in out

    def test_past_month_explains_the_missing_reconciliation(self, aug):
        assert "comparison skipped" in text_of(render_summary, aug)

    def test_pending_sync_is_separated_from_real_problems(self, september_html):
        report = parse_month(september_html)
        next(d for d in report.days if d.date.day == 8).report = []
        out = text_of(render_summary, analyse([report], today=TODAY))
        assert "Needs attention" in out
        assert "Waiting to sync" in out
        # The transient message must not sit under the actionable heading.
        actionable, pending = out.split("Waiting to sync")
        assert "ללא פרויקט" not in actionable

    def test_clock_freshness_is_shown(self, sept):
        assert "22/09 16:40" in text_of(render_summary, sept)

    def test_open_day_is_called_out(self, sept):
        assert "08:15\u2013\u2026" in text_of(render_summary, sept)


class TestDayTable:
    def test_every_day_has_a_row(self, sept):
        out = text_of(render_days, sept)
        assert all(f"{d:02d}/09" in out for d in range(1, 31))

    def test_future_days_carry_no_deficit(self, sept):
        out = text_of(render_days, sept)
        row = next(line for line in out.splitlines() if line.startswith("30/09"))
        assert "-9.00" not in row

    def test_today_in_progress_carries_no_deficit(self, sept):
        out = text_of(render_days, sept)
        row = next(line for line in out.splitlines() if line.startswith("22/09"))
        assert "-9.00" not in row

    def test_an_absence_with_no_times_is_not_rendered_as_a_range(self, sept):
        out = text_of(render_days, sept)
        row = next(line for line in out.splitlines() if line.startswith("20/09"))
        assert "—–—" not in row

    def test_two_segments_are_shown_side_by_side(self, sept):
        out = text_of(render_days, sept)
        row = next(line for line in out.splitlines() if line.startswith("07/09"))
        assert "20:00–20:45" in row


class TestJson:
    def test_core_numbers(self, sept):
        d = to_dict(sept)
        assert d["now"]["balance"] == "4.75"
        assert d["month_totals"]["remaining"] == "57.75"
        assert d["settled_through"] == "2026-09-21"

    def test_reconciliation_is_machine_readable(self, sept):
        assert to_dict(sept)["reconciliation"]["candidates"] == ["2026-09-08"]

    def test_past_month_has_no_reconciliation(self, aug):
        d = to_dict(aug)
        assert d["reconciliation"] is None
        assert d["hilan"]["applies_to_this_month"] is False

    def test_issues_carry_actionability(self, september_html):
        report = parse_month(september_html)
        next(d for d in report.days if d.date.day == 8).report = []
        kinds = {i["kind"]: i["actionable"]
                 for i in to_dict(analyse([report], today=TODAY))["issues"]}
        assert kinds["missing_clock_out"] is True
        assert kinds["pending_sync"] is False

    def test_every_day_is_present(self, sept):
        assert len(to_dict(sept)["days"]) == 30


class TestWeekWithNothingSettledYet:
    """Sunday morning: the week has begun but no day of it has landed.

    "WEEK 0.00 · required 0.00" reads as "you are square for the week", which is
    a different claim from "this week has no data yet". The tool exists to stop
    Hilan's numbers from quietly misleading; its own must not.
    """

    @pytest.fixture
    def empty_week(self, september_html):
        report = parse_month(september_html)
        # 27/09 is a Sunday; the marker still points at 22/09, so nothing in
        # the new week is settled.
        return analyse([report], today=date(2026, 9, 27))

    def test_the_week_has_no_settled_days(self, empty_week):
        assert empty_week.week_days == []

    def test_json_reports_it_too(self, empty_week):
        assert to_dict(empty_week)["week"]["days"] == []


class TestEnglishOutputAndHebrewOrder:
    """Everything the tool says is English; Hilan's Hebrew reads the right way."""

    def test_the_labels_are_english(self, sept):
        out = text_of(render_summary, sept)
        for label in ("NOW", "MONTH", "TODAY", "worked", "required", "to"):
            assert label in out, label

    def test_a_known_hebrew_message_is_translated(self, sept):
        out = text_of(render_summary, sept)
        assert "a row is reported without a project" in out
        assert "פרויקט" not in out

    def test_leave_symbols_are_translated_in_the_table(self, sept):
        out = text_of(render_days, sept)
        assert "vacation" in out
        assert "holiday" in out

    def test_a_free_text_comment_keeps_its_hebrew_but_reordered(self, sept):
        """Nobody should invent an English rendering of what a person typed."""
        from hilan.text import visual

        comment = "דיווח ידני"
        assert visual(comment) != comment


class TestZeroIsDecimalToo:
    """Zero is a duration like any other and prints as decimal hours too."""

    def test_a_zero_balance_prints_as_decimal(self):
        from hilan.render import _signed

        assert _signed(timedelta(0)).plain == "0.00"

    def test_no_colon_separated_duration_anywhere_in_the_output(self, sept):
        import re

        out = text_of(render_summary, sept) + text_of(render_days, sept)
        # Clock times are legitimate (07:55–17:05); a duration column is not.
        for row in out.splitlines():
            cells = row.split()
            for cell in cells:
                if re.fullmatch(r"[+-]\d+:\d\d", cell):
                    raise AssertionError(f"h:mm duration in output: {cell!r} ({row})")
        assert " 0:00 " not in out


class TestForecastLine:
    """The most-asked question gets its own line: when can I go home?"""

    @pytest.fixture
    def morning(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        r = with_entry(september_on_the_23rd(september_html), 8, 20)
        return analyse([r], today=date(2026, 9, 23),
                       now=datetime(2026, 9, 23, 12, 0))

    def test_it_shows_todays_hours_and_what_is_left(self, morning):
        """The total sits under the punches now, not on the day line."""
        out = text_of(render_summary, morning).splitlines()
        i = next(n for n, l in enumerate(out) if "TODAY" in l)
        block = "\n".join(out[i:i + 3])
        assert "4.42 of 9.00" in block    # 0.75 early + 3:40 since 08:20
        assert "4.58 to go" in block

    def test_it_names_the_time_to_close_the_day(self, morning):
        out = text_of(render_summary, morning)
        assert "16:35" in out

    def test_it_offers_the_earlier_time_that_spends_the_surplus(self, morning):
        out = text_of(render_summary, morning)
        assert "11:00" in out

    def test_without_an_open_punch_no_leave_time_is_invented(self, september_html):
        """The early session is finished, so there is nothing to count from."""
        from tests.test_forecast import september_on_the_23rd

        a = analyse([september_on_the_23rd(september_html)],
                    today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))
        out = text_of(render_summary, a)
        assert "TODAY" in out
        assert "leave" not in out
        assert a.forecast.leave_to_close_today is None

    def test_a_finished_day_says_so(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        r = with_entry(september_on_the_23rd(september_html), 8, 20)
        a = analyse([r], today=date(2026, 9, 23), now=datetime(2026, 9, 23, 19, 0))
        out = text_of(render_summary, a)
        assert "done for today" in out


class TestNoHebrewInTheDefaultHeader:
    """The employee name would be the only Hebrew on screen, and identifies nobody.

    In a terminal it renders correctly, pre-reversed. Pasted anywhere that does
    its own bidi — a chat window, a bug report — it reverses a second time and
    reads backwards, which makes a working tool look broken. Nothing is lost by
    leaving a personal tool's own account name out of its header.
    """

    def test_the_header_names_the_month_only(self, sept):
        head = next(l for l in text_of(render_summary, sept).splitlines() if "Hilan" in l)
        assert head.strip() == "Hilan · September 2026"

    def test_no_hebrew_survives_anywhere_in_the_summary(self, sept):
        import re

        out = text_of(render_summary, sept)
        assert not re.search(r"[֐-׿]", out), out

    def test_the_number_is_still_in_the_json(self, sept):
        assert to_dict(sept)["employee"] == "12345"


class TestTodayShowsItsPunches:
    """The times are the thing being checked; the total alone hides them.

    "7.11 of 9.00" does not say whether the day started at 08:20 or at noon, and
    that is what a glance at today is usually for.
    """

    def line(self, analysis, label="TODAY"):
        """The TODAY block: the day line and everything stacked under it."""
        out = text_of(render_summary, analysis).splitlines()
        i = next(n for n, l in enumerate(out) if label in l)
        return out[i], "\n".join(out[i:i + 3])

    def test_closed_and_open_punches_are_both_shown(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        a = analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                    today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))
        head, block = self.line(a)
        assert "06:00–06:45" in head          # the early session, finished
        assert "08:20" in block               # still running, on its own line

    def test_the_day_total_sits_under_them(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        a = analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                    today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))
        _, block = self.line(a)
        assert "4.42 of 9.00" in block

    def test_with_only_a_morning_entry_it_shows_just_that(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        report = september_on_the_23rd(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report = []                       # drop the early session
        a = analyse([with_entry(report, 8, 20)], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 12, 0))
        head, _ = self.line(a)
        assert "08:20" in head
        assert "06:00" not in head

    def test_a_day_with_nothing_yet_says_so(self, september_html):
        from tests.test_forecast import september_on_the_23rd

        report = september_on_the_23rd(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report, day.clock = [], []
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 12, 0))
        head, _ = self.line(a)
        assert "nothing recorded yet" in head

    def test_an_open_punch_is_marked_as_still_running(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        a = analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                    today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))
        _, block = self.line(a)
        assert "08:20–…" in block


class TestTheHeaderHasARule:
    def test_a_rule_sits_under_the_title(self, sept):
        lines = [l for l in text_of(render_summary, sept).splitlines() if l.strip()]
        assert "─" in lines[1]


class TestPunchesStack:
    """One punch per line, aligned under the first, rather than run together."""

    def lines(self, analysis):
        out = text_of(render_summary, analysis).splitlines()
        i = next(n for n, l in enumerate(out) if "TODAY" in l)
        return out[i:i + 3]

    @pytest.fixture
    def morning(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        return analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                       today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))

    def test_the_first_punch_shares_the_day_line(self, morning):
        first = self.lines(morning)[0]
        assert "23/09" in first and "06:00–06:45" in first

    def test_the_second_punch_is_on_its_own_line(self, morning):
        second = self.lines(morning)[1]
        assert "08:20–…" in second
        assert "23/09" not in second

    def test_it_lines_up_under_the_first(self, morning):
        first, second = self.lines(morning)[:2]
        assert first.index("06:00") == second.index("08:20")

    def test_the_days_figures_sit_below_the_punches(self, morning):
        assert "4.42 of 9.00" in self.lines(morning)[2]

    def test_a_single_punch_needs_no_second_line(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        report = september_on_the_23rd(september_html)
        next(d for d in report.days if d.date == date(2026, 9, 23)).report = []
        a = analyse([with_entry(report, 8, 20)], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 12, 0))
        assert "08:20–…" in self.lines(a)[0]
        assert "to go" in self.lines(a)[1]


class TestEachPunchCarriesItsOwnHours:
    """A stacked punch without its hours makes you subtract times in your head."""

    def block(self, analysis):
        out = text_of(render_summary, analysis).splitlines()
        i = next(n for n, l in enumerate(out) if "TODAY" in l)
        return out[i:i + 3]

    @pytest.fixture
    def morning(self, september_html):
        from tests.test_forecast import september_on_the_23rd, with_entry

        return analyse([with_entry(september_on_the_23rd(september_html), 8, 20)],
                       today=date(2026, 9, 23), now=datetime(2026, 9, 23, 12, 0))

    def test_a_finished_punch_shows_what_it_was_worth(self, morning):
        first = self.block(morning)[0]
        assert "06:00–06:45" in first and "0.75" in first

    def test_a_running_punch_shows_the_time_so_far(self, morning):
        second = self.block(morning)[1]
        assert "08:20–…" in second and "3.67" in second      # 08:20 to 12:00

    def test_the_running_one_is_marked_as_such(self, morning):
        assert "running" in self.block(morning)[1]

    def test_the_day_total_moves_to_the_summary_line(self, morning):
        third = self.block(morning)[2]
        assert "4.42 of 9.00" in third

    def test_the_first_line_no_longer_carries_the_day_total(self, morning):
        assert "4.42 of 9.00" not in self.block(morning)[0]

    def test_the_hours_column_lines_up(self, morning):
        first, second = self.block(morning)[:2]
        assert first.index("0.75") == second.index("3.67")

    def test_every_line_fits_a_narrow_terminal(self, morning):
        for line in text_of(render_summary, morning).splitlines():
            assert len(line) <= 80, (len(line), line)


class TestTheInHintIsOnlyForAMissingPunch:
    """Suggesting --in when the punches are right there reads as an instruction.

    With 06:00-06:45 and 08:20-14:05 both showing and both finished, "not
    clocked in — pass --in HH:MM" underneath would be advice to supply something
    the tool can plainly see. The hint is for a punch Hilan has not synced yet.
    """

    def block(self, analysis):
        out = text_of(render_summary, analysis).splitlines()
        i = next(n for n, l in enumerate(out) if "TODAY" in l)
        return "\n".join(out[i:i + 4])

    def test_a_finished_session_gets_no_hint(self, september_html):
        from datetime import time as _t

        from hilan.parser import ReportSegment
        from tests.test_forecast import september_on_the_23rd

        report = september_on_the_23rd(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report.append(ReportSegment(
            entry=_t(8, 20), exit=_t(14, 5), total=timedelta(hours=5, minutes=45),
            standard=None, comment="", symbol="נוכחות"))
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 17, 0))
        block = self.block(a)
        assert "08:20" in block
        assert "--in" not in block
        assert "to go" in block

    def test_a_day_with_nothing_recorded_still_gets_it(self, september_html):
        from tests.test_forecast import september_on_the_23rd

        report = september_on_the_23rd(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 23))
        day.report, day.clock = [], []
        a = analyse([report], today=date(2026, 9, 23),
                    now=datetime(2026, 9, 23, 9, 0))
        assert "--in" in self.block(a)


class TestTheBankIsReadableAtAnyMoment:
    """Two true numbers, each labelled so neither can be read as the other."""

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
        return analyse([report], today=date(2026, 9, 23),
                       now=datetime(2026, 9, 23, 17, 0))

    def line(self, analysis, label):
        return next(l for l in text_of(render_summary, analysis).splitlines()
                    if label in l)

    def test_the_live_figure_leads(self, afternoon):
        out = [l for l in text_of(render_summary, afternoon).splitlines() if l.strip()]
        first = next(l for l in out if "NOW" in l or "BANKED" in l)
        assert "NOW" in first and "+3.08" in first

    def test_it_says_the_number_is_conditional(self, afternoon):
        assert "stopping now" in self.line(afternoon, "NOW")

    def test_the_banked_figure_keeps_its_own_line_and_date(self, afternoon):
        banked = self.line(afternoon, "BANKED")
        assert "+5.58" in banked and "22/09" in banked

    def test_one_line_only_when_they_agree(self, august_html):
        out = text_of(render_summary, analyse([parse_month(august_html)],
                                              today=date(2026, 9, 23)))
        assert "BANKED" in out
        assert "stopping now" not in out

    def test_a_shortfall_shows_as_the_smaller_number(self, afternoon):
        """The whole point: 5.58 banked, 3.08 if you walk out now."""
        assert "+3.08" in self.line(afternoon, "NOW")
        assert "+5.58" in self.line(afternoon, "BANKED")
