# -*- coding: utf-8 -*-
"""The command line, exercised end to end without touching the network."""
import json

import httpx

import pytest
from click.testing import CliRunner

from hilan.cli import _months_needed, _parse_month_arg, main
from datetime import date

#: What the month POST answers with: the attendance page, grid and all.
MONTH_PAGE = '<html><div id="calendar_container"></div>the month</html>'


@pytest.fixture
def sept_file(tmp_path, september_html):
    p = tmp_path / "2026-09.html"
    p.write_text(september_html, encoding="utf-8")
    return p


@pytest.fixture
def aug_file(tmp_path, august_html):
    p = tmp_path / "2026-08.html"
    p.write_text(august_html, encoding="utf-8")
    return p


@pytest.fixture
def runner():
    return CliRunner()


def run(runner, *args):
    result = runner.invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


class TestSummaryCommand:
    def test_offline_summary(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22")
        assert "+4.75" in out

    def test_day_table_flag(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22", "--days")
        assert "01/09" in out and "30/09" in out

    def test_json_flag(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22", "--json")
        payload = json.loads(out)
        assert payload["now"]["balance"] == "4.75"

    def test_strict_attendance_drops_the_credit(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22",
                  "--strict-attendance", "--json")
        assert json.loads(out)["now"]["credited"] == "0.00"

    def test_two_files_are_merged(self, runner, sept_file, aug_file):
        out = run(runner, "--from-file", str(aug_file), "--from-file", str(sept_file),
                  "--today", "2026-09-02", "--json")
        payload = json.loads(out)
        # The week of 02/09 starts on 30/08, so August has to be in scope.
        assert payload["week"]["start"] == "2026-08-30"
        assert payload["week"]["in_scope"] is True

    def test_missing_file_is_rejected(self, runner):
        result = runner.invoke(main, ["--from-file", "/nope/missing.html"])
        assert result.exit_code != 0


class TestSubcommands:
    def test_help_lists_login_and_fetch(self, runner):
        out = run(runner, "--help")
        assert "login" in out and "fetch" in out


class TestMonthArgument:
    @pytest.mark.parametrize("value, expected", [("2026-09", (2026, 9)), ("2026-01", (2026, 1))])
    def test_valid(self, value, expected):
        assert _parse_month_arg(value) == expected

    @pytest.mark.parametrize("value", ["2026", "2026-13", "sept", "", "2026-9-1"])
    def test_invalid(self, value):
        import click
        with pytest.raises(click.BadParameter):
            _parse_month_arg(value)


class TestMonthsNeeded:
    def test_midweek_current_month_needs_only_itself(self):
        assert _months_needed((2026, 9), date(2026, 9, 22)) == [(2026, 9)]

    def test_week_reaching_back_pulls_the_previous_month(self):
        # 02/09/2026 is a Wednesday; its week starts on 30/08.
        assert _months_needed((2026, 9), date(2026, 9, 2)) == [(2026, 8), (2026, 9)]

    def test_explicit_past_month_is_fetched_alone(self):
        assert _months_needed((2026, 8), date(2026, 9, 22)) == [(2026, 8)]


class TestWeekFlag:
    def test_week_table_covers_only_the_current_week(self, runner, sept_file):
        """The wide table shows clock times, so those identify which days it holds."""
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22", "--week")
        assert "08:15" in out          # 22/09, inside the week
        assert "07:55" not in out      # 01/09, outside it

    def test_week_and_days_can_be_combined(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22",
                  "--week", "--days")
        assert out.count("30/09") >= 1


class TestMissingCredentials:
    def test_non_interactive_run_explains_itself(self, runner, monkeypatch):
        import hilan.config as cfg
        from hilan.client import HilanClient, LoginFailed

        # The session is gone, so the fetch fails and credentials are needed.
        def expired(self, year, month):
            raise LoginFailed("session expired")

        monkeypatch.setattr(HilanClient, "fetch_month", expired)
        monkeypatch.setattr(cfg, "load_username", lambda: None)
        monkeypatch.delenv("HILAN_PASSWORD", raising=False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        result = runner.invoke(main, ["--today", "2026-09-22"])
        assert result.exit_code != 0
        assert "hilan login" in result.output


class TestForecastFlags:
    """--in supplies a punch Hilan has not synced yet; --now pins the clock."""

    def test_in_flag_produces_a_leave_time(self, runner, sept_file):
        """24/09 has no punch, so the supplied one is used and marked assumed."""
        out = run(runner, "--from-file", str(sept_file),
                  "--today", "2026-09-24", "--now", "12:00", "--in", "08:20")
        assert "leave 16:50 to close (assumed)" in out       # 08:20 + 8.50

    def test_a_real_punch_beats_the_supplied_one(self, runner, sept_file):
        """22/09 is clocked in at 08:15 for real; --in must not override it."""
        out = run(runner, "--from-file", str(sept_file),
                  "--today", "2026-09-22", "--now", "12:00", "--in", "06:00")
        assert "08:15\u2013\u2026" in out            # the real punch, still running
        assert "(assumed)" not in out

    def test_a_bad_time_is_rejected_clearly(self, runner, sept_file):
        result = runner.invoke(
            main, ["--from-file", str(sept_file), "--in", "half past eight"]
        )
        assert result.exit_code != 0
        assert "HH:MM" in result.output

    def test_without_the_flag_no_time_is_invented(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file),
                  "--today", "2026-09-24", "--now", "12:00")
        assert "not clocked in \u2014 pass --in HH:MM" in out


class TestTheDefaultIsFull:
    def test_everything_is_there_without_a_flag(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22")
        for block in ("NOW", "MONTH", "TODAY", "worked", "required"):
            assert block in out, block

    def test_brief_cuts_it_back_to_two_numbers(self, runner, sept_file):
        out = run(runner, "--brief", "--from-file", str(sept_file), "--today", "2026-09-22")
        assert "WORKED" in out and "BANKED" in out
        assert "required" not in out and "TODAY" not in out

    def test_the_day_table_flag_still_gives_the_wide_table(self, runner, sept_file):
        out = run(runner, "--days", "--from-file", str(sept_file), "--today", "2026-09-22")
        assert "07:55" in out          # the wide table shows clock times


class TestTheSessionRenewsItself:
    """A session can lapse between the check and the fetch, so check once more.

    is_authenticated() passing is not a promise that the next request will be
    served: Hilan can drop the session in between, and then the fetch lands on
    /login. Failing there would ask the user to run a command the tool can run
    itself, with a password it already holds in the Keychain.
    """

    def transport(self, *, fail_first):
        state = {"fetched": 0, "logins": 0}

        def handler(request):
            if request.url.path.endswith("/LoginApi/LoginRequest"):
                state["logins"] += 1
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            if request.url.path == "/login":
                return httpx.Response(200, text='"OrgId":"1000"')
            if request.method == "POST":
                return httpx.Response(200, text=MONTH_PAGE)
            state["fetched"] += 1
            if fail_first and state["fetched"] == 1:
                return httpx.Response(302, headers={"location": "/login?isOnSelf=true"})
            return httpx.Response(
                200,
                text='<html><div id="calendar_container"></div><form>'
                '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" />'
                "</form></html>",
            )

        return httpx.MockTransport(handler), state

    def test_a_lapsed_session_is_renewed_and_the_fetch_retried(self, monkeypatch):
        from hilan.cli import _fetch_months
        from hilan.client import HilanClient

        transport, state = self.transport(fail_first=True)
        monkeypatch.setattr(
            "hilan.cli._authenticate", lambda client, force=False: None
        )
        with HilanClient(transport=transport) as client:
            pages = _fetch_months(client, [(2026, 9)])
        assert pages == [MONTH_PAGE]
        assert state["fetched"] == 2          # failed once, then succeeded

    def test_a_healthy_session_is_not_disturbed(self, monkeypatch):
        from hilan.cli import _fetch_months
        from hilan.client import HilanClient

        transport, state = self.transport(fail_first=False)
        calls = []
        monkeypatch.setattr(
            "hilan.cli._authenticate", lambda client, force=False: calls.append(force)
        )
        with HilanClient(transport=transport) as client:
            _fetch_months(client, [(2026, 9)])
        assert state["fetched"] == 1
        assert calls == []

    def test_it_gives_up_after_one_retry(self, monkeypatch):
        """Two failures in a row is a real problem, not a lapsed session."""
        from hilan.cli import _fetch_months
        from hilan.client import HilanClient, LoginFailed

        def handler(request):
            if request.url.path.endswith("/LoginApi/LoginRequest"):
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            if request.url.path == "/login":
                return httpx.Response(200, text='"OrgId":"1000"')
            return httpx.Response(302, headers={"location": "/login?isOnSelf=true"})

        monkeypatch.setattr(
            "hilan.cli._authenticate", lambda client, force=False: None
        )
        with HilanClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(LoginFailed):
                _fetch_months(client, [(2026, 9)])


class TestNoWastedRoundTrip:
    """Checking the session first would cost a request that says nothing new.

    fetch_month's own GET already reveals whether the session is alive — it has
    to look at that page anyway — and a lapsed one is retried. Asking first would
    put a second round trip on every run against a server that takes seconds to
    answer.
    """

    def requests_made(self, monkeypatch, *, session_ok=True):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            if request.url.path.endswith("/LoginApi/LoginRequest"):
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            if request.url.path == "/login":
                return httpx.Response(200, text='"OrgId":"1000"')
            if request.method == "POST":
                return httpx.Response(200, text=MONTH_PAGE)
            if not session_ok and len([s for s in seen if s[0] == "GET"]) == 1:
                return httpx.Response(302, headers={"location": "/login?isOnSelf=true"})
            return httpx.Response(
                200,
                text='<html><div id="calendar_container"></div><form>'
                '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" />'
                "</form></html>",
            )

        from hilan.cli import _fetch_months
        from hilan.client import HilanClient

        monkeypatch.setattr("hilan.cli._authenticate", lambda c, force=False: None)
        with HilanClient(transport=httpx.MockTransport(handler)) as client:
            _fetch_months(client, [(2026, 9)])
        return seen

    def attendance(self, seen):
        """Only the hits on the page itself; redirects are not round trips we chose."""
        return [m for m, path in seen if path.endswith("calendarpage.aspx")]

    def test_a_live_session_costs_one_get_and_one_post(self, monkeypatch):
        assert self.attendance(self.requests_made(monkeypatch)) == ["GET", "POST"]

    def test_a_lapsed_one_pays_for_the_retry_and_no_more(self, monkeypatch):
        seen = self.requests_made(monkeypatch, session_ok=False)
        assert self.attendance(seen) == ["GET", "GET", "POST"]


class TestHistoryIsKept:
    def store(self, tmp_path, monkeypatch):
        path = tmp_path / "history.json"
        monkeypatch.setattr("hilan.history.HISTORY_FILE", path)
        return path

    def test_a_run_records_its_settled_days(self, runner, sept_file, tmp_path, monkeypatch):
        from hilan import history

        path = self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file))
        kept = history.load(path=path)
        assert "2026-09-01" in kept
        assert "2026-09-23" not in kept          # today is never settled

    def test_the_history_command_totals_what_was_kept(self, runner, sept_file, tmp_path, monkeypatch):
        self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file))
        out = run(runner, "history")
        assert "2026-09" in out

    def test_an_empty_history_says_so(self, runner, tmp_path, monkeypatch):
        self.store(tmp_path, monkeypatch)
        out = run(runner, "history")
        assert "nothing recorded yet" in out

    def test_an_amended_day_is_reported_on_the_next_run(self, runner, sept_file, tmp_path, monkeypatch):
        import json as j

        path = self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file))
        kept = j.loads(path.read_text())
        kept["2026-09-01"]["worked"] = "7.00"
        path.write_text(j.dumps(kept))
        out = run(runner, "--from-file", str(sept_file))
        assert "01/09" in out
        assert "7.00" in out and "9.17" in out


class TestAPretendDateDoesNotRewriteHistory:
    """--today and --now fabricate the cut, so what they settle is not real.

    Recording them would write partial months into the history, with nothing
    to say the month was incomplete. A run that pretends it is another day must
    not leave anything behind.
    """

    def store(self, tmp_path, monkeypatch):
        path = tmp_path / "history.json"
        monkeypatch.setattr("hilan.history.HISTORY_FILE", path)
        return path

    def test_today_override_records_nothing(self, runner, sept_file, tmp_path, monkeypatch):
        from hilan import history

        path = self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file), "--today", "2026-09-10")
        assert history.load(path=path) == {}

    def test_now_override_records_nothing(self, runner, sept_file, tmp_path, monkeypatch):
        from hilan import history

        path = self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file), "--now", "09:00")
        assert history.load(path=path) == {}

    def test_an_ordinary_run_still_records(self, runner, sept_file, tmp_path, monkeypatch):
        from hilan import history

        path = self.store(tmp_path, monkeypatch)
        run(runner, "--from-file", str(sept_file))
        assert history.load(path=path) != {}


class TestANetworkFailureReadsLikeAMessage:
    """Hilan is slow, and a slow answer must not show a Python traceback.

    A timeout surfaces as httpx.ReadTimeout with a stack through contextlib and
    httpx internals. None of that tells the person at the terminal anything
    they can act on, and the traceback belongs in the log where it can be
    investigated.
    """

    def failing(self, error):
        def handler(request):
            raise error

        return httpx.MockTransport(handler)

    def run_with(self, runner, monkeypatch, error, tmp_path):
        from hilan.client import HilanClient

        monkeypatch.setattr("hilan.history.HISTORY_FILE", tmp_path / "history.json")
        real = HilanClient.__init__

        def patched(self, transport=None):
            real(self, transport=self.__class__._transport)

        monkeypatch.setattr(HilanClient, "_transport", self.failing(error), raising=False)
        monkeypatch.setattr(HilanClient, "__init__", patched)
        monkeypatch.setattr("hilan.cli._authenticate", lambda c, force=False: None)
        return runner.invoke(main, [], catch_exceptions=False)

    def test_a_timeout_is_explained_not_dumped(self, runner, monkeypatch, tmp_path):
        result = self.run_with(
            runner, monkeypatch, httpx.ReadTimeout("timed out"), tmp_path
        )
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "httpx" not in result.output
        assert "did not answer" in result.output

    def test_a_refused_connection_too(self, runner, monkeypatch, tmp_path):
        result = self.run_with(
            runner, monkeypatch, httpx.ConnectError("refused"), tmp_path
        )
        assert result.exit_code != 0
        assert "Traceback" not in result.output

    def test_the_traceback_still_reaches_the_log(self, runner, monkeypatch, tmp_path):
        from hilan import log as hlog

        logfile = tmp_path / "hilan.log"
        monkeypatch.setattr(hlog, "LOG_FILE", logfile)
        hlog.reset()
        self.run_with(runner, monkeypatch, httpx.ReadTimeout("timed out"), tmp_path)
        text = logfile.read_text()
        assert "Traceback" in text and "ReadTimeout" in text
        hlog.reset()


class TestLoginWithoutAPrompt:
    """A script can log in without a prompt: --stdin, or HILAN_PASSWORD."""

    def _client(self, monkeypatch, seen):
        from hilan.client import HilanClient

        def fake_login(self, creds, verification_code=None):
            seen["password"] = creds.password

        monkeypatch.setattr(HilanClient, "login", fake_login)
        monkeypatch.setattr(HilanClient, "__init__", lambda self, transport=None, base=None: None)
        monkeypatch.setattr(HilanClient, "__enter__", lambda self: self)
        monkeypatch.setattr(HilanClient, "__exit__", lambda self, *a: False)

    def test_stdin_is_read_when_asked(self, runner, monkeypatch, tmp_path):
        seen = {}
        self._client(monkeypatch, seen)
        monkeypatch.setattr("hilan.config.CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr("hilan.config.ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr("hilan.config.load_username", lambda: "12345")
        monkeypatch.setattr("hilan.config._keyring", lambda: None)
        monkeypatch.setattr(
            "getpass.getpass", lambda *a, **k: pytest.fail("getpass was used")
        )
        result = runner.invoke(main, ["login", "--stdin"], input="hunter2\n")
        assert result.exit_code == 0, result.output
        assert seen["password"] == "hunter2"

    def test_the_environment_is_used_when_set(self, runner, monkeypatch, tmp_path):
        seen = {}
        self._client(monkeypatch, seen)
        monkeypatch.setattr("hilan.config.CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr("hilan.config.ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr("hilan.config.load_username", lambda: "12345")
        monkeypatch.setattr("hilan.config._keyring", lambda: None)
        monkeypatch.setenv("HILAN_PASSWORD", "from-env")
        monkeypatch.setattr(
            "getpass.getpass", lambda *a, **k: pytest.fail("getpass was used")
        )
        result = runner.invoke(main, ["login"])
        assert result.exit_code == 0, result.output
        assert seen["password"] == "from-env"

    def test_it_says_where_the_password_went(self, runner, monkeypatch, tmp_path):
        seen = {}
        self._client(monkeypatch, seen)
        monkeypatch.setattr("hilan.config.CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr("hilan.config.ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr("hilan.config.load_username", lambda: "12345")
        monkeypatch.setattr("hilan.config._keyring", lambda: None)
        result = runner.invoke(main, ["login", "--stdin"], input="hunter2\n")
        assert ".env" in result.output


class TestTheSayFlag:
    """--say prints the standing as one sentence, for Siri or a notification."""

    def test_it_is_one_line(self, runner, sept_file):
        out = run(runner, "--say", "--from-file", str(sept_file), "--today", "2026-09-22")
        assert len(out.strip().splitlines()) == 1

    def test_it_takes_the_other_options(self, runner, sept_file):
        out = run(runner, "--say", "--from-file", str(sept_file),
                  "--today", "2026-09-24", "--now", "12:00", "--in", "08:20")
        assert "leave at 16:50" in out

    def test_there_is_no_say_subcommand(self, runner):
        result = runner.invoke(main, ["say"])
        assert result.exit_code != 0


class TestHebrewSurvivesAWindowsPipe:
    """A pipe on Windows is written in the ANSI code page, which has no Hebrew."""

    def test_a_non_utf8_stream_is_switched(self, monkeypatch):
        import io
        import sys

        from hilan.cli import _utf8_output

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        monkeypatch.setattr(sys, "stdout", stream)
        _utf8_output()
        stream.write("חופשה")
        stream.flush()
        assert raw.getvalue().decode("utf-8") == "חופשה"

    def test_a_stream_that_cannot_be_switched_is_left_alone(self, monkeypatch):
        import io
        import sys

        from hilan.cli import _utf8_output

        stream = io.StringIO()          # no reconfigure, no encoding
        monkeypatch.setattr(sys, "stdout", stream)
        _utf8_output()                  # must not raise
        assert sys.stdout is stream

    def test_the_json_carries_the_hebrew_as_written(self, runner, sept_file):
        out = run(runner, "--from-file", str(sept_file), "--today", "2026-09-22", "--json")
        assert json.loads(out)["days"][10]["special"] == "ערב חג"

    def test_every_run_switches_it(self, runner, sept_file, monkeypatch):
        calls = []
        monkeypatch.setattr("hilan.cli._utf8_output", lambda: calls.append(True))
        run(runner, "--from-file", str(sept_file), "--today", "2026-09-22", "--json")
        assert calls == [True]
