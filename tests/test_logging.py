# -*- coding: utf-8 -*-
"""A log you can investigate a failure from, and would not mind someone reading.

Every run leaves a trace: what was asked for, every request and how long it took,
what came back, what was computed, and the whole traceback when something breaks.

What must never reach it is as important as what must: the password, the session
cookies, and Hilan's __VIEWSTATE. A log is the thing people paste into a chat
when they ask for help.
"""
import logging
import re
from datetime import date

import httpx
import pytest

from conftest import posix_modes
from hilan import log as hlog


@pytest.fixture
def logfile(tmp_path, monkeypatch):
    path = tmp_path / "hilan.log"
    monkeypatch.setattr(hlog, "LOG_FILE", path)
    hlog.reset()
    yield path
    hlog.reset()


class TestItRecordsTheRun:
    def test_the_command_is_logged(self, logfile):
        hlog.setup(argv=["hilan", "--days", "--month", "2026-09"])
        hlog.logger().info("working")
        assert "--days" in logfile.read_text()

    def test_a_request_is_logged_with_its_timing(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.request_done("GET", "https://x/Hilannetv2/Attendance/calendarpage.aspx",
                          200, 0.42, 12345)
        text = logfile.read_text()
        assert "GET" in text and "200" in text and "0.42" in text and "12345" in text

    def test_a_traceback_is_kept_whole(self, logfile):
        hlog.setup(argv=["hilan"])
        try:
            raise ValueError("boom")
        except ValueError:
            hlog.failure("while fetching")
        text = logfile.read_text()
        assert "while fetching" in text
        assert "ValueError: boom" in text
        assert "Traceback" in text

    def test_lines_carry_a_timestamp_and_level(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.logger().warning("careful")
        line = [l for l in logfile.read_text().splitlines() if "careful" in l][0]
        assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", line), line
        assert "WARNING" in line


class TestWhatMustNeverAppear:
    SECRETS = {
        "password": "not-a-real-password!",
        "viewstate": "/wEPDwUKFAKEVIEWSTATEFORTESTS0000",
        "cookie": "TS01abcdef=0123456789abcdef",
    }

    def test_a_password_is_never_written(self, logfile):
        hlog.setup(argv=["hilan", "login"])
        hlog.request_done(
            "POST", "https://x/HilanCenter/Public/api/LoginApi/LoginRequest",
            200, 0.5, 100,
            body=f"orgId=1000&username=12345&password={self.SECRETS['password']}",
        )
        assert self.SECRETS["password"] not in logfile.read_text()

    def test_the_viewstate_is_never_written(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.request_done("POST", "https://x/a.aspx", 200, 0.5, 100,
                          body=f"__VIEWSTATE={self.SECRETS['viewstate']}&x=1")
        assert self.SECRETS["viewstate"] not in logfile.read_text()

    def test_the_event_validation_token_is_never_written(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.request_done("POST", "https://x/a.aspx", 200, 0.5, 100,
                          body="__VIEWSTATE=v&__EVENTVALIDATION=/wEdAAFAKETOKEN0000&x=1")
        assert "FAKETOKEN" not in logfile.read_text()

    def test_cookies_are_never_written(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.logger().info("headers: %s", hlog.safe({"Cookie": self.SECRETS["cookie"]}))
        assert "0123456789abcdef" not in logfile.read_text()

    def test_the_redacted_field_is_still_visible_as_a_field(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.request_done("POST", "https://x/y", 200, 0.1, 10,
                          body="username=12345&password=secret")
        text = logfile.read_text()
        assert "username=12345" in text          # useful, and not a secret
        assert "password=<redacted>" in text


class TestTheFileItself:
    @posix_modes
    def test_it_is_private(self, logfile):
        hlog.setup(argv=["hilan"])
        hlog.logger().info("x")
        assert oct(logfile.stat().st_mode)[-3:] == "600"

    def test_it_does_not_grow_without_bound(self, logfile):
        hlog.setup(argv=["hilan"], max_bytes=2048, keep=2)
        for n in range(500):
            hlog.logger().info("a line that takes up some room %d", n)
        sizes = [p.stat().st_size for p in logfile.parent.glob("hilan.log*")]
        assert sizes, "nothing was written"
        assert max(sizes) <= 4096
        assert len(sizes) <= 3

    def test_a_directory_it_cannot_write_does_not_break_the_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hlog, "LOG_FILE", tmp_path / "nope" / "x" / "hilan.log")
        monkeypatch.setattr("pathlib.Path.mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError))
        hlog.reset()
        hlog.setup(argv=["hilan"])          # must not raise
        hlog.logger().info("still fine")


class TestTheRunIsTraceable:
    """End to end: a real run leaves enough behind to investigate it."""

    def test_a_successful_run_logs_its_requests_and_numbers(
        self, runner_and_file
    ):
        runner, sept_file, logfile = runner_and_file
        from hilan.cli import main

        result = runner.invoke(
            main, ["--from-file", str(sept_file), "--today", "2026-09-22"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        text = logfile.read_text()
        assert "run: " in text
        assert "--from-file" in text
        assert "settled through 2026-09-21" in text
        assert "balance 4.75" in text

    def test_a_failing_run_logs_the_traceback(self, runner_and_file, monkeypatch):
        runner, sept_file, logfile = runner_and_file
        from hilan.cli import main

        monkeypatch.setattr(
            "hilan.cli.analyse",
            lambda *a, **k: (_ for _ in ()).throw(ZeroDivisionError("bad sums")),
        )
        runner.invoke(main, ["--from-file", str(sept_file)])
        text = logfile.read_text()
        assert "Traceback" in text
        assert "ZeroDivisionError: bad sums" in text

    def test_verbose_also_prints_where_it_can_be_seen(self, runner_and_file):
        runner, sept_file, logfile = runner_and_file
        from hilan.cli import main

        result = runner.invoke(
            main, ["--verbose", "--from-file", str(sept_file), "--today", "2026-09-22"],
            catch_exceptions=False,
        )
        assert "settled through" in result.output


@pytest.fixture
def runner_and_file(tmp_path, monkeypatch, september_html):
    from click.testing import CliRunner

    path = tmp_path / "hilan.log"
    monkeypatch.setattr(hlog, "LOG_FILE", path)
    monkeypatch.setattr("hilan.history.HISTORY_FILE", tmp_path / "history.json")
    hlog.reset()
    sept = tmp_path / "sept.html"
    sept.write_text(september_html, encoding="utf-8")
    yield CliRunner(), sept, path
    hlog.reset()
