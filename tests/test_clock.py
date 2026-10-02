# -*- coding: utf-8 -*-
"""Hilan counts in Israel time, so the tool does too, wherever it runs."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

from hilan import clock


@pytest.mark.real_clock
class TestTheClock:
    def test_it_is_israel_time_to_the_minute(self):
        expected = datetime.now(ZoneInfo("Asia/Jerusalem")).replace(tzinfo=None)
        got = clock.now()
        assert got.second == 0 and got.microsecond == 0 and got.tzinfo is None
        assert abs(got - expected.replace(second=0, microsecond=0)) <= timedelta(minutes=1)

    def test_whatever_the_machine_says(self, monkeypatch):
        """A UTC server at 22:30 is already tomorrow in Israel."""
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                moment = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        monkeypatch.setattr(clock, "datetime", Frozen)
        assert clock.now() == datetime(2026, 9, 23, 1, 30)
        assert clock.today() == date(2026, 9, 23)


class TestTheCommandUsesIt:
    def test_today_comes_from_israel_time(self, monkeypatch, september_html, tmp_path):
        from hilan.cli import main

        monkeypatch.setattr("hilan.clock.now", lambda: datetime(2026, 9, 23, 1, 30))
        page = tmp_path / "sept.html"
        page.write_text(september_html, encoding="utf-8")
        out = CliRunner().invoke(main, ["--from-file", str(page), "--json"]).output
        import json
        assert json.loads(out)["today"] == "2026-09-23"

    def test_a_malformed_today_is_a_clear_error(self, september_html, tmp_path):
        from hilan.cli import main

        page = tmp_path / "sept.html"
        page.write_text(september_html, encoding="utf-8")
        result = CliRunner().invoke(main, ["--from-file", str(page), "--today", "22/09/2026"])
        assert result.exit_code != 0 and "YYYY-MM-DD" in result.output
        assert "Traceback" not in result.output
