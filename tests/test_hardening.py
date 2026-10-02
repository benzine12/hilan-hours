# -*- coding: utf-8 -*-
"""Protections that are easy to lose, each held in place by a test.

Logging in from a script, not locking the account, refusing anything that is
not plainly a read, keeping files private from their first byte, keeping the
history whole, and saying what went wrong instead of printing a traceback.
"""
import io
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from click.testing import CliRunner

from conftest import TEST_URL, posix_modes
from hilan import config, history
from hilan.calc import analyse
from hilan.cli import main
from hilan.client import HilanClient, ReadOnlyViolation, build_client, redact
from hilan.parser import ReportSegment, parse_month

ATTENDANCE = "/Hilannetv2/Attendance/calendarpage.aspx"
LOGIN = "/HilanCenter/Public/api/LoginApi/LoginRequest"
PAGE = ('<html><div id="calendar_container"></div><form>'
        '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" /></form></html>')


class FakeHilan:
    """Just enough of Hilan for the command line to talk to."""

    def __init__(self, *, session=True, login_ok=True, month_html=PAGE):
        self.session, self.login_ok, self.month_html = session, login_ok, month_html
        self.logins, self.posts, self.gets = [], 0, 0
        #: The month POST after which the session lapses, if any.
        self.lapse_after_post = None

    def __call__(self, request):
        path = request.url.path
        if path == "/login":
            return httpx.Response(200, text='"OrgId":"1000"')
        if path == LOGIN:
            self.logins.append(parse_qs(request.content.decode()))
            if self.login_ok:
                self.session = True
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            return httpx.Response(200, json={"IsFail": True, "Code": 1,
                                             "ErrorMessage": "wrong password"})
        if not self.session:
            return httpx.Response(302, headers={"location": "/login"})
        if request.method == "POST":
            self.posts += 1
            if self.posts == self.lapse_after_post:
                self.session = False
                return httpx.Response(302, headers={"location": "/login"})
            return httpx.Response(200, text=self.month_html)
        self.gets += 1
        return httpx.Response(200, text=PAGE)


@pytest.fixture
def hilan(monkeypatch):
    """Point the command line at a fake Hilan, and return it."""
    fake = FakeHilan()
    real = HilanClient

    def client(base=None, transport=None):
        return real(transport=httpx.MockTransport(fake), base=base)

    monkeypatch.setattr("hilan.cli.HilanClient", client)
    return fake


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sept_file(tmp_path, september_html):
    page = tmp_path / "sept.html"
    page.write_text(september_html, encoding="utf-8")
    return page


# -- logging in from a script ------------------------------------------------

class TestLoggingInFromAScript:
    def test_stdin_never_supplies_the_employee_number(self, runner, hilan, monkeypatch):
        """With nothing configured, the password must not be taken for the number."""
        monkeypatch.delenv("HILAN_USER", raising=False)
        result = runner.invoke(main, ["login", "--stdin"], input="Tr0ub4dor&3\n")
        assert result.exit_code != 0 and "--user" in result.output
        assert hilan.logins == []                       # nothing was sent

    def test_the_number_can_be_given(self, runner, hilan):
        result = runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="s3cret\n")
        assert result.exit_code == 0, result.output
        assert hilan.logins[0]["username"] == ["12345"]
        assert hilan.logins[0]["password"] == ["s3cret"]

    def test_a_windows_line_ending_is_not_part_of_the_password(self, runner, hilan):
        runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="s3cret\r\n")
        assert hilan.logins[0]["password"] == ["s3cret"]

    def test_the_log_names_the_fields_and_nothing_else(self, runner, hilan, private_config):
        runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="s3cret\n")
        text = (private_config / "hilan.log").read_text(encoding="utf-8")
        assert "LoginRequest" in text and "password=" in text
        assert "s3cret" not in text and "username=12345" not in text

    def test_a_plain_text_store_is_said_out_loud(self, runner, hilan, no_keyring):
        result = runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="s3cret\n")
        assert "plain-text" in result.output


# -- not locking the account ---------------------------------------------------

class TestARefusedPasswordIsNotRetried:
    """Sending a refused password again on every scheduled run locks the account."""

    @pytest.fixture
    def refusing(self, hilan, monkeypatch):
        hilan.session, hilan.login_ok = False, False
        monkeypatch.setenv("HILAN_USER", "12345")
        monkeypatch.setenv("HILAN_PASSWORD", "old-password")
        return hilan

    def test_the_first_refusal_is_reported(self, runner, refusing):
        result = runner.invoke(main, [])
        assert result.exit_code != 0 and "hilan login" in result.output
        assert "Traceback" not in result.output
        assert len(refusing.logins) == 1

    def test_later_runs_do_not_try_again(self, runner, refusing):
        runner.invoke(main, [])
        result = runner.invoke(main, [])
        assert len(refusing.logins) == 1
        assert "hilan login" in result.output

    def test_logging_in_by_hand_clears_it(self, runner, refusing):
        runner.invoke(main, [])
        refusing.login_ok = True
        result = runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="new\n")
        assert result.exit_code == 0, result.output
        assert config.refused_since() is None


# -- nothing but a read --------------------------------------------------------

READ = {"__EVENTTARGET": "", "__calendarSelectedDays": "9740",
        "ctl00$mp$currentMonth": "01/09/2026", "ctl00$mp$RefreshSelectedDays": "x"}


@pytest.fixture
def client():
    with build_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))) as c:
        yield c


class TestTheGuard:
    @pytest.mark.parametrize("url", [
        "http://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx",
        "https://example.net.hilan.co.il:8443/Hilannetv2/Attendance/calendarpage.aspx",
    ])
    def test_only_https_on_the_standard_port(self, client, url):
        with pytest.raises(ReadOnlyViolation):
            client.get(url)

    @pytest.mark.parametrize("path", [
        "/HilanCenter/Public/api/LoginApi/%2e%2e/%2e%2e/%2e%2e/%2e%2e/Hilannetv2/Attendance/calendarpage.aspx",
        "/HilanCenter/Public/api/LoginApi/ResendSms",
        "/Hilannetv2//Attendance/calendarpage.aspx",
        # Decodes to the attendance path exactly; only the raw form gives it away.
        "/Hilannetv2/Attendance/calendarpage%2Easpx",
    ])
    def test_no_path_but_the_two_exact_ones(self, client, path):
        with pytest.raises(ReadOnlyViolation):
            client.post(TEST_URL + path, data=READ)

    def test_a_login_that_changes_the_password_is_a_write(self, client):
        with pytest.raises(ReadOnlyViolation):
            client.post(TEST_URL + LOGIN, data={"username": "x", "password": "y",
                                                "isChangePassword": "true", "newPassword": "z"})
        with pytest.raises(ReadOnlyViolation):
            client.post(TEST_URL + LOGIN, data={"username": "x", "newPassword": "z"})

    def test_an_ordinary_login_passes(self, client):
        assert client.post(TEST_URL + LOGIN, data={
            "username": "x", "password": "y", "isChangePassword": "false", "newPassword": ""
        }).status_code == 200

    @pytest.mark.parametrize("extra", [
        {"ctl00$mp$Something": "1"},
        {"__EVENTTARGET": "ctl00$mp$RG_Days$ctl02"},
        {"ctl00$mp$RefreshPeriod": "x"},                   # two read actions
    ])
    def test_an_attendance_post_carries_nothing_a_read_does_not(self, client, extra):
        with pytest.raises(ReadOnlyViolation):
            client.post(TEST_URL + ATTENDANCE, data={**READ, **extra})

    def test_event_validation_may_ride_along(self, client):
        assert client.post(TEST_URL + ATTENDANCE,
                           data={**READ, "__EVENTVALIDATION": "abc"}).status_code == 200

    def test_a_redirect_elsewhere_is_refused_before_it_is_followed(self):
        def handler(request):
            if request.url.host == "evil.example":
                pytest.fail("the redirect reached another host")
            return httpx.Response(307, headers={"location": "https://evil.example/steal"})

        with build_client(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(ReadOnlyViolation):
                c.post(TEST_URL + LOGIN, data={"username": "x", "password": "y"})


# -- saying what went wrong ------------------------------------------------------

class TestFailuresReadAsMessages:
    def _check(self, result, *words):
        assert result.exit_code != 0
        assert "Traceback" not in result.output
        for word in words:
            assert word in result.output, result.output

    def test_a_page_that_is_not_an_attendance_page(self, runner, tmp_path):
        page = tmp_path / "login.html"
        page.write_text("<html><form>login</form></html>", encoding="utf-8")
        self._check(runner.invoke(main, ["--from-file", str(page)]), "not a Hilan attendance page")

    def test_an_empty_file(self, runner, tmp_path):
        page = tmp_path / "empty.html"
        page.write_text("", encoding="utf-8")
        self._check(runner.invoke(main, ["--from-file", str(page)]), "not a Hilan attendance page")

    def test_a_file_that_is_not_utf8(self, runner, tmp_path):
        page = tmp_path / "old.html"
        page.write_bytes("חופשה".encode("cp1255"))
        self._check(runner.invoke(main, ["--from-file", str(page)]), "UTF-8")

    def test_a_redirect_to_another_host(self, runner, monkeypatch):
        real = HilanClient

        def handler(request):
            return httpx.Response(302, headers={"location": "https://sso.example/login"})

        monkeypatch.setattr("hilan.cli.HilanClient",
                            lambda base=None, transport=None: real(
                                transport=httpx.MockTransport(handler), base=base))
        self._check(runner.invoke(main, []), "refused to send a request")

    def test_hilan_not_answering_during_login(self, runner, monkeypatch):
        real = HilanClient

        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        monkeypatch.setattr("hilan.cli.HilanClient",
                            lambda base=None, transport=None: real(
                                transport=httpx.MockTransport(handler), base=base))
        self._check(runner.invoke(main, ["login", "--stdin", "--user", "1"], input="x\n"),
                    "did not answer")

    def test_a_wrong_verification_code(self, runner, monkeypatch):
        from hilan.client import LoginFailed, VerificationRequired

        calls = []

        def login(self, creds, verification_code=None):
            calls.append(verification_code)
            if verification_code is None:
                raise VerificationRequired("code sent")
            raise LoginFailed("the code is wrong")

        monkeypatch.setattr(HilanClient, "login", login)
        monkeypatch.setattr(HilanClient, "__init__", lambda self, transport=None, base=None: None)
        monkeypatch.setattr(HilanClient, "__enter__", lambda self: self)
        monkeypatch.setattr(HilanClient, "__exit__", lambda self, *a: False)
        result = runner.invoke(main, ["login", "--stdin", "--user", "1"], input="x\n000000\n")
        self._check(result, "the code is wrong")

    def test_a_history_that_cannot_be_written_does_not_stop_the_run(
            self, runner, sept_file, monkeypatch):
        def refuse(*a, **k):
            raise PermissionError(13, "Permission denied", "history.json")

        monkeypatch.setattr("hilan.history.record", refuse)
        result = runner.invoke(main, ["--from-file", str(sept_file)])
        assert result.exit_code == 0, result.output
        assert "history not recorded" in result.output


# -- the history, whatever the output ---------------------------------------------

class TestEveryRunKeepsTheHistory:
    def _amend(self, sept_file, runner, private_config):
        runner.invoke(main, ["--from-file", str(sept_file)])
        store = private_config / "history.json"
        kept = json.loads(store.read_text(encoding="utf-8"))
        kept["2026-09-01"]["worked"] = "7.00"
        store.write_text(json.dumps(kept), encoding="utf-8")

    def test_json_records_and_reports_amendments(self, runner, sept_file, private_config):
        self._amend(sept_file, runner, private_config)
        out = runner.invoke(main, ["--from-file", str(sept_file), "--json"]).output
        payload = json.loads(out)
        assert payload["amendments"][0]["date"] == "2026-09-01"
        assert payload["amendments"][0]["was"]["worked"] == "7.00"

    def test_say_still_tells_of_an_amendment(self, runner, sept_file, private_config):
        self._amend(sept_file, runner, private_config)
        result = runner.invoke(main, ["--from-file", str(sept_file), "--say"])
        # The sentence alone on standard output; the news beside it on stderr.
        assert result.stdout.strip().count("\n") == 0
        assert "Hilan has changed days already recorded" in result.stderr

    def test_strict_attendance_leaves_the_history_alone(self, runner, sept_file, private_config):
        runner.invoke(main, ["--from-file", str(sept_file)])
        before = (private_config / "history.json").read_text(encoding="utf-8")
        result = runner.invoke(main, ["--from-file", str(sept_file), "--strict-attendance"])
        assert "changed days" not in result.output
        assert (private_config / "history.json").read_text(encoding="utf-8") == before

    def test_an_unreadable_history_is_kept_aside_not_overwritten(
            self, runner, sept_file, private_config):
        store = private_config / "history.json"
        store.write_text('{"2026-08-03": {"worked": "9.00"', encoding="utf-8")   # truncated
        result = runner.invoke(main, ["--from-file", str(sept_file)])
        assert "was unreadable" in result.output
        aside = list(private_config.glob("history.json.unreadable-*"))
        assert len(aside) == 1 and '"2026-08-03"' in aside[0].read_text(encoding="utf-8")
        assert "2026-09-01" in history.load(path=store)

    def test_no_temporary_file_is_left_behind(self, runner, sept_file, private_config):
        runner.invoke(main, ["--from-file", str(sept_file)])
        assert not list(private_config.glob("*.tmp"))


# -- the week across a month boundary -------------------------------------------------

class TestTheWeekTable:
    def test_it_shows_the_days_from_the_month_before(self, runner, sept_file, tmp_path, august_html):
        aug = tmp_path / "aug.html"
        aug.write_text(august_html, encoding="utf-8")
        out = runner.invoke(main, ["--from-file", str(aug), "--from-file", str(sept_file),
                                   "--today", "2026-09-02", "--week"]).output
        assert "30/08" in out and "31/08" in out and "05/09" in out


# -- fetch -----------------------------------------------------------------------------

class TestFetch:
    def test_a_bad_month_is_refused_before_hilan_is_asked(self, runner, hilan, tmp_path):
        result = runner.invoke(main, ["fetch", "--month", "2026-09", "--month", "2026-13",
                                      "--out", str(tmp_path / "out")])
        assert result.exit_code != 0
        assert hilan.gets == 0 and hilan.posts == 0

    def test_pages_are_saved_redacted_and_private(self, runner, hilan, tmp_path):
        hilan.month_html = PAGE.replace("/wEPDwUB", "SECRET-STATE")
        out = tmp_path / "out"
        result = runner.invoke(main, ["fetch", "--month", "2026-09", "--out", str(out)])
        assert result.exit_code == 0, result.output
        saved = (out / "2026-09.html").read_text(encoding="utf-8")
        assert "SECRET-STATE" not in saved and "REDACTED" in saved
        if sys.platform != "win32":
            assert oct((out / "2026-09.html").stat().st_mode)[-3:] == "600"

    def test_raw_keeps_the_state(self, runner, hilan, tmp_path):
        hilan.month_html = PAGE.replace("/wEPDwUB", "SECRET-STATE")
        out = tmp_path / "out"
        runner.invoke(main, ["fetch", "--month", "2026-09", "--raw", "--out", str(out)])
        assert "SECRET-STATE" in (out / "2026-09.html").read_text(encoding="utf-8")

    def test_a_session_lapsing_halfway_is_renewed_once(self, runner, hilan, tmp_path, monkeypatch):
        """August is kept, not fetched again; only September is asked for twice."""
        monkeypatch.setenv("HILAN_USER", "12345")
        monkeypatch.setenv("HILAN_PASSWORD", "pw")
        hilan.lapse_after_post = 2
        out = tmp_path / "out"
        result = runner.invoke(main, ["fetch", "--month", "2026-08", "--month", "2026-09",
                                      "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert len(hilan.logins) == 1
        assert hilan.posts == 3                      # August once, September twice
        assert sorted(p.name for p in out.iterdir()) == ["2026-08.html", "2026-09.html"]


# -- files that are private from their first byte --------------------------------------

@posix_modes
class TestPrivateFiles:
    def test_a_new_file_is_never_readable_by_others(self, tmp_path, monkeypatch):
        seen = []
        real_open = os.open

        def watching(path, flags, mode=0o777, *a, **k):
            seen.append(mode)
            return real_open(path, flags, mode, *a, **k)

        monkeypatch.setattr(os, "open", watching)
        config.write_private(tmp_path / "secret", "x")
        assert seen == [0o600]
        assert oct((tmp_path / "secret").stat().st_mode)[-3:] == "600"

    def test_the_config_directory_is_closed_to_others(self, tmp_path):
        target = tmp_path / "cfg"
        target.mkdir(mode=0o755)
        config.ensure_private_dir(target)
        assert oct(target.stat().st_mode)[-3:] == "700"

    def test_a_rotated_log_stays_private(self, tmp_path, monkeypatch):
        from hilan import log as hlog

        monkeypatch.setattr(hlog, "LOG_FILE", tmp_path / "hilan.log")
        hlog.setup(argv=["hilan"], max_bytes=300, keep=2)
        for n in range(60):
            hlog.logger().info("a line long enough to roll the log over %d", n)
        hlog.reset()
        files = list(tmp_path.glob("hilan.log*"))
        assert len(files) >= 2
        assert all(oct(f.stat().st_mode)[-3:] == "600" for f in files)


# -- the .env reads back what was written -------------------------------------------------

class TestTheEnvFileRoundTrips:
    @pytest.mark.parametrize("password", [
        "plain", " leading", "trailing ", "'quoted'", '"double"', "with # hash",
        "back\\slash", "a=b=c", 'mixed " and \' and \\',
    ])
    def test_any_password(self, no_keyring, password):
        config.password_set("12345", password)
        assert config.password_get("12345") == password


# -- the client -----------------------------------------------------------------------------

class TestTheClient:
    def test_the_password_stays_out_of_repr(self):
        assert "s3cret" not in repr(config.Credentials(username="1", password="s3cret"))

    @pytest.mark.parametrize("content", [b"null", b"\xff\xfe\x00", b'[1, "x", {"name": 1}]', b"{}"])
    def test_a_garbled_cookie_file_is_no_session(self, private_config, content):
        (private_config / "cookies.json").write_bytes(content)
        c = HilanClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert len(c._http.cookies.jar) == 0
        c._http.close()

    def test_a_login_page_that_cannot_be_fetched_is_an_error_not_an_empty_id(self):
        def handler(request):
            raise httpx.ConnectError("down", request=request)

        c = HilanClient(transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.ConnectError):
            c.org_id()

    def test_the_month_post_must_land_on_the_attendance_page(self):
        from hilan.client import LoginFailed

        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, text="<html>login again</html>")
            return httpx.Response(200, text=PAGE)

        c = HilanClient(transport=httpx.MockTransport(handler))
        with pytest.raises(LoginFailed):
            c.fetch_month(2026, 9)

    def test_event_validation_is_handed_back(self):
        sent = {}

        def handler(request):
            if request.method == "POST":
                sent.update(parse_qs(request.content.decode()))
                return httpx.Response(200, text=PAGE)
            return httpx.Response(200, text=PAGE.replace(
                "</form>", '<input type="hidden" name="__EVENTVALIDATION" value="EV1" /></form>'))

        HilanClient(transport=httpx.MockTransport(handler)).fetch_month(2026, 9)
        assert sent["__EVENTVALIDATION"] == ["EV1"]

    @pytest.mark.parametrize("tag", [
        '<input type="hidden" name="__VIEWSTATE" value="SECRET" />',
        '<input type="hidden" value="SECRET" name="__VIEWSTATE" />',
        "<input type='hidden' name='__VIEWSTATE' value='SECRET' />",
        '<INPUT NAME="H-XSRF-Token" VALUE="SECRET">',
        '<input name="ctl00$Password" value="SECRET" type="password">',
    ])
    def test_redaction_does_not_depend_on_attribute_order(self, tag):
        out = redact(f"<form>{tag}</form>")
        assert "SECRET" not in out and "REDACTED" in out

    def test_redaction_leaves_ordinary_inputs_alone(self):
        from bs4 import BeautifulSoup

        tag = '<input name="ctl00$mp$currentMonth" value="01/09/2026" />'
        kept = BeautifulSoup(redact(tag), "html.parser").find("input")
        assert kept["name"] == "ctl00$mp$currentMonth" and kept["value"] == "01/09/2026"


# -- arithmetic ---------------------------------------------------------------------------------

class TestTheArithmetic:
    def _today(self, september_html, segments, clock=()):
        report = parse_month(september_html)
        day = next(d for d in report.days if d.date == date(2026, 9, 22))
        day.report, day.clock, day.idle_standards = segments, list(clock), []
        return report

    def test_no_early_exit_is_offered_against_a_deficit(self, september_html):
        report = self._today(september_html, [ReportSegment(
            entry=time(9, 0), exit=None, total=None, standard=Decimal("9.00"),
            comment="", symbol="נוכחות")])
        for d in report.days:                        # wipe the month: a deep deficit
            if d.date < date(2026, 9, 22):
                d.report, d.clock = [], []
        f = analyse([report], today=date(2026, 9, 22), now=datetime(2026, 9, 22, 9, 30)).forecast
        assert f.leave_to_end_level is None
        assert f.leave_to_close_today == time(18, 0)

    def test_a_row_past_midnight_covers_a_punch_inside_it(self, september_html):
        from hilan.parser import ClockSegment
        report = self._today(
            september_html,
            [ReportSegment(entry=time(23, 0), exit=time(7, 0), total=timedelta(hours=8),
                           standard=Decimal("9.00"), comment="", symbol="נוכחות")],
            clock=[ClockSegment(entry=time(23, 30), exit=None)],
        )
        f = analyse([report], today=date(2026, 9, 22), now=datetime(2026, 9, 22, 23, 59)).forecast
        assert f.entry is None and f.worked_so_far == timedelta(hours=8)

    @pytest.mark.parametrize("text, expected", [
        ("12.50-", Decimal("-12.50")), ("-12.50", Decimal("-12.50")), ("12.50", Decimal("12.50")),
    ])
    def test_a_minus_sign_on_either_side(self, text, expected):
        from hilan.parser import _parse_decimal
        assert _parse_decimal(text) == expected

    def test_the_legend_reads_a_trailing_minus(self, september_html):
        page = september_html.replace("הינה: -12.50", "הינה: 12.50-")
        assert parse_month(page).totals.vacation_balance == Decimal("-12.50")


class TestTheOutput:
    def test_a_gap_the_other_way_has_one_sign(self, september_html):
        from rich.console import Console
        from hilan.parser import HilanTotals
        from hilan.render import render_summary

        report = parse_month(september_html)
        t = report.totals
        report.totals = HilanTotals(standard=t.standard, standard_to_date=t.standard_to_date,
                                    actual=Decimal("110.25"), productive=Decimal("110.25"),
                                    vacation_balance=t.vacation_balance)
        console = Console(record=True, width=120, force_terminal=False)
        render_summary(analyse([report], today=date(2026, 9, 22)), console)
        out = console.export_text()
        assert "gap +4.00" in out and "--" not in out.replace("──", "")

    def test_missing_totals_are_null_in_the_json(self, september_html):
        from hilan.parser import HilanTotals
        from hilan.render import to_dict

        report = parse_month(september_html)
        report.totals = HilanTotals(standard=None, standard_to_date=None, actual=None,
                                    productive=None, vacation_balance=None)
        d = to_dict(analyse([report], today=date(2026, 9, 22)))
        assert d["hilan"]["actual"] is None and d["hilan"]["standard"] is None


# -- logging in by hand, files elsewhere, what the log says -----------------------

class TestALoginTypedByHand:
    def test_a_refusal_stops_the_automatic_logins_too(self, runner, hilan):
        """Hilan counts this try as well: nothing may follow it on its own."""
        hilan.login_ok = False
        result = runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="typo\n")
        assert result.exit_code != 0
        assert config.refused_since() is not None

    def test_a_401_from_the_login_is_a_refusal(self, runner, monkeypatch):
        from hilan.client import Rejected

        def handler(request):
            if request.url.path == LOGIN:
                return httpx.Response(401)
            return httpx.Response(200, text='"OrgId":"1000"')

        with HilanClient(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(Rejected, match="401"):
                c.login(config.Credentials(username="1", password="x"))

    def test_a_password_piped_in_is_read_as_utf8(self, runner, hilan, monkeypatch):
        # As a Windows console would: its own code page, not UTF-8.
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("pässwörd\n".encode()), encoding="cp1252"))
        from hilan import cli

        assert cli._stdin_line() == "pässwörd"

    def test_the_store_that_would_not_take_it_is_named(self, runner, hilan, monkeypatch):
        monkeypatch.setattr(config, "keyring_set", lambda *a: (_ for _ in ()).throw(config.StoreFailed("x")))
        result = runner.invoke(main, ["login", "--stdin", "--user", "12345"], input="s3cret\n")
        assert "would not take it" in result.output


class TestAnEnvFileElsewhere:
    @posix_modes
    def test_its_folder_is_left_as_it_was(self, no_keyring, tmp_path, monkeypatch):
        shared = tmp_path / "project"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        monkeypatch.setenv("HILAN_ENV", str(shared / ".env"))
        config.password_set("12345", "hunter2")
        assert oct(shared.stat().st_mode)[-3:] == "755"
        assert oct((shared / ".env").stat().st_mode)[-3:] == "600"


class TestWhatTheLogSaysWasRun:
    def test_flags_as_they_are_typed(self, runner, sept_file, private_config):
        runner.invoke(main, ["--from-file", str(sept_file), "--today", "2026-09-22", "--json"])
        line = next(l for l in (private_config / "hilan.log").read_text(encoding="utf-8").splitlines()
                    if "run:" in l)
        assert "--today 2026-09-22" in line and "--json" in line
        assert "today-arg" not in line and "as-json" not in line

    def test_the_subcommand(self, runner, private_config):
        runner.invoke(main, ["history"])
        text = (private_config / "hilan.log").read_text(encoding="utf-8")
        assert "run: hilan history" in text


class TestFilesAreWrittenAsGiven:
    def test_line_endings_stay_unix(self, private_config):
        """Windows' C runtime would turn each \\n into \\r\\n in a text-mode descriptor."""
        target = private_config / "x.json"
        config.write_private(target, "a\nb\n")
        assert target.read_bytes() == b"a\nb\n"


class TestTwoGridsOnOnePage:
    def test_each_day_once(self, september_html, august_html):
        from hilan.parser import parse_month

        both = september_html.replace("</body>", august_html.split("<body>", 1)[1])
        days = parse_month(both).days
        assert len(days) == len({d.date for d in days})
