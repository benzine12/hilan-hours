# -*- coding: utf-8 -*-
"""A local record of every settled day, kept across runs.

Only settled days are written. An unsettled one is still moving, and a history
that rewrites itself is not a history. Hilan can also amend a day long after the
fact — approve a missing exit, change a requirement — so a day that comes back
different from what was stored is reported rather than quietly overwritten,
which is the same silent change this tool exists to catch.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from conftest import posix_modes
from hilan import history
from hilan.calc import analyse
from hilan.parser import parse_month

TODAY = date(2026, 9, 23)


@pytest.fixture
def store(tmp_path):
    return tmp_path / "history.json"


@pytest.fixture
def sept(september_html):
    from tests.test_forecast import september_on_the_23rd

    return analyse([september_on_the_23rd(september_html)], today=TODAY,
                   now=datetime(2026, 9, 23, 12, 0))


class TestWhatGetsWritten:
    def test_settled_days_are_stored(self, sept, store):
        history.record(sept, path=store)
        kept = history.load(path=store)
        assert "2026-09-01" in kept
        assert kept["2026-09-01"]["worked"] == "9.17"
        assert kept["2026-09-01"]["required"] == "9.00"
        assert kept["2026-09-01"]["balance"] == "0.17"

    def test_today_is_not_stored(self, sept, store):
        history.record(sept, path=store)
        assert "2026-09-23" not in history.load(path=store)

    def test_nor_is_any_day_past_the_cut(self, sept, store):
        history.record(sept, path=store)
        assert max(history.load(path=store)) == "2026-09-22"

    def test_a_days_note_is_kept(self, sept, store):
        history.record(sept, path=store)
        assert "vacation" in history.load(path=store)["2026-09-14"]["note"]

    @posix_modes
    def test_the_file_is_private(self, sept, store):
        history.record(sept, path=store)
        assert oct(store.stat().st_mode)[-3:] == "600"


class TestItAccumulates:
    def test_a_second_run_keeps_the_first(self, sept, store):
        history.record(sept, path=store)
        before = dict(history.load(path=store))
        history.record(sept, path=store)
        assert history.load(path=store) == before

    def test_days_from_another_month_are_added_not_replaced(self, sept, august_html, store):
        history.record(sept, path=store)
        aug = analyse([parse_month(august_html)], today=TODAY,
                      now=datetime(2026, 9, 23, 12, 0))
        history.record(aug, path=store)
        kept = history.load(path=store)
        assert "2026-08-03" in kept and "2026-09-01" in kept

    def test_a_corrupt_file_does_not_lose_the_run(self, sept, store):
        store.write_text("{ not json")
        history.record(sept, path=store)
        assert "2026-09-01" in history.load(path=store)


class TestAmendments:
    def test_a_changed_day_is_reported(self, sept, store):
        history.record(sept, path=store)
        kept = json.loads(store.read_text())
        kept["2026-09-01"]["worked"] = "7.00"
        store.write_text(json.dumps(kept))

        changed = history.record(sept, path=store)
        assert [c.day for c in changed] == [date(2026, 9, 1)]
        assert changed[0].was["worked"] == "7.00"
        assert changed[0].now["worked"] == "9.17"

    def test_the_new_figures_win(self, sept, store):
        history.record(sept, path=store)
        kept = json.loads(store.read_text())
        kept["2026-09-01"]["worked"] = "7.00"
        store.write_text(json.dumps(kept))
        history.record(sept, path=store)
        assert history.load(path=store)["2026-09-01"]["worked"] == "9.17"

    def test_an_unchanged_run_reports_nothing(self, sept, store):
        history.record(sept, path=store)
        assert history.record(sept, path=store) == []


class TestReadingItBack:
    def test_totals_per_month(self, sept, august_html, store):
        history.record(sept, path=store)
        history.record(analyse([parse_month(august_html)], today=TODAY,
                               now=datetime(2026, 9, 23, 12, 0)), path=store)
        rows = history.by_month(path=store)
        assert [r.label for r in rows] == ["2026-08", "2026-09"]
        sep = next(r for r in rows if r.label == "2026-09")
        assert sep.days == 22                      # 01/09-22/09
        assert sep.worked == Decimal("116.08")
        assert sep.credited == Decimal("9.00")
        assert sep.balance == Decimal("5.58")

    def test_the_overall_total_is_the_sum_of_the_months(self, sept, august_html, store):
        history.record(sept, path=store)
        history.record(analyse([parse_month(august_html)], today=TODAY,
                               now=datetime(2026, 9, 23, 12, 0)), path=store)
        rows = history.by_month(path=store)
        assert history.total(path=store).worked == sum(r.worked for r in rows)

    def test_an_empty_history_reads_as_nothing(self, store):
        assert history.by_month(path=store) == []
        assert history.total(path=store).worked == Decimal("0")


class TestThePunchesAreKeptToo:
    """Totals answer "how much"; only the punches answer "when".

    A day that reads 10.58 against a 9.00 requirement says nothing about whether
    it started at 07:00 or ended at 21:00, and that is usually the thing being
    checked months later.
    """

    def test_entry_and_exit_are_recorded(self, sept, store):
        history.record(sept, path=store)
        assert history.load(path=store)["2026-09-22"]["punches"] == ["08:15-18:05"]

    def test_several_segments_are_all_kept(self, sept, store):
        history.record(sept, path=store)
        assert history.load(path=store)["2026-09-07"]["punches"] == [
            "08:00-17:05", "20:00-20:45",
        ]

    def test_a_day_with_no_punches_records_none(self, sept, store):
        history.record(sept, path=store)
        assert history.load(path=store)["2026-09-14"]["punches"] == []

    def test_a_missing_exit_is_visible_rather_than_dropped(self, sept, store):
        from hilan.parser import ReportSegment
        from datetime import time as _t

        day = next(d for d in sept.days if d.date == date(2026, 9, 21))
        day.record.report = [ReportSegment(entry=_t(9, 0), exit=None, total=None,
                                           standard=None, comment="", symbol="נוכחות")]
        history.record(sept, path=store)
        assert history.load(path=store)["2026-09-21"]["punches"] == ["09:00-?"]

    def test_changed_punches_count_as_an_amendment(self, sept, store):
        import json as j

        history.record(sept, path=store)
        kept = j.loads(store.read_text())
        kept["2026-09-22"]["punches"] = ["08:15-17:00"]
        store.write_text(j.dumps(kept))
        changed = history.record(sept, path=store)
        assert [c.day for c in changed] == [date(2026, 9, 22)]
