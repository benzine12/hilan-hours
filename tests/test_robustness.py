# -*- coding: utf-8 -*-
"""Smaller things that would each go wrong quietly: one test apiece."""
import json
import logging
import os
import sys
import threading
import time as clock
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from click.testing import CliRunner

from conftest import posix_modes
from hilan import config, history
from hilan import log as hlog
from hilan.calc import IssueKind, analyse
from hilan.cli import main
from hilan.client import HilanClient, ReadOnlyViolation, build_client, redact
from hilan.parser import parse_month
from hilan.text import visual
from test_edge_days import BLANK, _row, at, needs_node, same_in_the_widget, september

ATTENDANCE = "https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx?isOnSelf=true"
READ = {"__calendarSelectedDays": "9740", "ctl00$mp$RefreshSelectedDays": "x"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def client():
    with build_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        yield c


class TestTheGuardReadsWhatASPNETReads:
    def test_a_percent_u_escape_in_a_body(self, client):
        # %u0062tnSave is "btnSave" to ASP.NET; to everything here it is not.
        with pytest.raises(ReadOnlyViolation, match="%u"):
            client.post(ATTENDANCE, headers=FORM,
                        content=b"__calendarSelectedDays=9740&ctl00%24mp%24RefreshSelectedDays=x"
                                b"&__EVENTARGUMENT=%u0062tnSave")

    def test_a_percent_u_escape_in_a_query(self, client):
        with pytest.raises(ReadOnlyViolation, match="%u"):
            client.get(ATTENDANCE + "&__%u0056IEWSTATE=x")

    @pytest.mark.parametrize("field", ["__CALLBACKID", "__CALLBACKPARAM", "__PREVIOUSPAGE"])
    def test_a_callback_in_a_get(self, client, field):
        with pytest.raises(ReadOnlyViolation):
            client.get(f"{ATTENDANCE}&{field}=x")

    def test_a_save_button_as_a_query_value(self, client):
        with pytest.raises(ReadOnlyViolation):
            client.get(ATTENDANCE + "&next=ctl00$mp$RG$btnSave")


class TestAVerificationCodeOnAScheduledRun:
    """Nobody is there to type it: each run would only send another SMS."""

    def test_it_stops_instead_of_asking(self, monkeypatch):
        from hilan.client import VerificationRequired

        sent = []

        class Hilan:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def is_authenticated(self): return False
            def login(self, creds, verification_code=None):
                sent.append(verification_code)
                raise VerificationRequired("code sent")

        monkeypatch.setattr("hilan.cli.HilanClient", Hilan)
        monkeypatch.setattr(config, "resolve_credentials",
                            lambda: config.Credentials(username="1", password="x"))
        from hilan.cli import _authenticate

        with pytest.raises(config.CredentialsUnavailable, match="verification code"):
            _authenticate(Hilan())
        assert sent == [None]                           # asked once, no code prompt
        assert "verification" in config.refused_since()
        with pytest.raises(config.CredentialsUnavailable):
            _authenticate(Hilan())
        assert sent == [None]                           # and not again


class TestTheStopItself:
    def test_a_folder_it_cannot_write_is_a_warning_not_a_crash(self, monkeypatch, caplog):
        monkeypatch.setattr(config, "write_private", lambda *a: (_ for _ in ()).throw(PermissionError()))
        with caplog.at_level(logging.WARNING, logger="hilan"):
            config.mark_refused()
        assert "could not record" in caplog.text


class TestTheEnvFile:
    @pytest.mark.parametrize("password", ["two\nlines", "carriage\rreturn", "both\r\nends", "\n"])
    def test_a_line_break_round_trips(self, no_keyring, password):
        config.password_set("12345", password)
        assert config.password_get("12345") == password


class TestWritingAFile:
    def test_two_writers_at_once(self, private_config):
        target = private_config / "x.json"
        errors = []

        def write(n):
            try:
                for _ in range(50):
                    config.write_private(target, json.dumps({"n": n}))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert json.loads(target.read_text())["n"] in range(4)
        assert [p.name for p in private_config.iterdir() if p.name.endswith(".tmp")] == []

    def test_a_failed_rename_leaves_no_temporary_file(self, private_config, monkeypatch):
        monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(OSError):
            config.write_private(private_config / "y.json", "{}")
        assert [p.name for p in private_config.iterdir() if p.name.endswith(".tmp")] == []

    @posix_modes
    def test_it_is_private_from_the_start(self, private_config):
        config.write_private(private_config / "z.json", "{}")
        assert oct((private_config / "z.json").stat().st_mode)[-3:] == "600"


class TestSavedPages:
    def test_an_input_name_in_another_case_is_still_scrubbed(self):
        page = '<input type="hidden" name="__viewstate" value="SECRETSTATE">'
        assert "SECRETSTATE" not in redact(page)


class TestTheLog:
    def test_a_secret_in_a_url_is_redacted(self, private_config):
        hlog.setup(argv=["hilan"])
        hlog.request_done("GET", "https://x/y?password=hunter2&a=1", 200, 0.1)
        assert "hunter2" not in (private_config / "hilan.log").read_text(encoding="utf-8")

    @posix_modes
    def test_older_rotated_logs_are_made_private(self, private_config):
        old = private_config / "hilan.log.1"
        old.write_text("old\n")
        old.chmod(0o644)
        hlog.reset()
        hlog.setup(argv=["hilan"])
        assert oct(old.stat().st_mode)[-3:] == "600"


class TestCookies:
    def test_an_expired_one_is_not_loaded(self, private_config):
        (private_config / "cookies.json").write_text(json.dumps([
            {"name": "old", "value": "1", "domain": "example.net.hilan.co.il", "path": "/",
             "expires": int(clock.time()) - 60},
            {"name": "live", "value": "2", "domain": "example.net.hilan.co.il", "path": "/",
             "expires": int(clock.time()) + 3600},
        ]))
        c = HilanClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert sorted(k.name for k in c._http.cookies.jar) == ["live"]
        c._http.close()

    def test_the_expiry_is_kept(self, private_config):
        c = HilanClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        later = int(clock.time()) + 3600
        c._http.cookies.set("sid", "s", domain="example.net.hilan.co.il", path="/")
        next(iter(c._http.cookies.jar)).expires = later
        c._save_cookies()
        c._http.close()
        assert json.loads((private_config / "cookies.json").read_text())[0]["expires"] == later


class TestHistorySetAside:
    def test_another_run_moving_it_first(self, private_config, monkeypatch):
        target = private_config / "history.json"
        target.write_text("{ broken")
        real = type(target).replace

        def gone(self, other):
            self.unlink()                    # the other run got there first
            raise FileNotFoundError(str(self))

        monkeypatch.setattr(type(target), "replace", gone)
        assert history._load_for_update(target) == {}


class TestTheParser:
    def test_a_one_digit_day(self):
        html = september(html_edit=lambda h: h.replace('ov="05/09 שבת"', 'ov="5/09 שבת"'))
        assert date(2026, 9, 5) in {d.date for d in parse_month(html).days}

    @needs_node
    def test_the_widget_reads_it_too(self, tmp_path):
        html = september(html_edit=lambda h: h.replace('ov="07/09 יום ב"', 'ov="7/09 יום ב"'))
        same_in_the_widget(tmp_path, html, datetime(2026, 9, 22, 12, 0))


class TestHebrewAroundEnglish:
    @pytest.mark.parametrize("text,shown", [
        ("שלום hi there עולם", "םלוע hi there םולש"),
        ("שלום 12 34 עולם", "םלוע 34 12 םולש"),          # numbers are not one run
        # A number after a Latin word takes its direction: one run (bidi rule W7).
        ("שלום abc 12 עולם", "םלוע abc 12 םולש"),
        ("חג worked 9:10 חג", "גח worked 9:10 גח"),
        # A number first, then a word: two runs, shown right to left.
        ("שלום 12 abc עולם", "םלוע abc 12 םולש"),
    ])
    def test_the_words_keep_their_order(self, monkeypatch, text, shown):
        monkeypatch.delenv("HILAN_BIDI", raising=False)
        assert visual(text) == shown


class TestReconciliation:
    def test_hilan_counting_more_is_not_put_down_to_today(self):
        """Today's unseen hours explain our side being ahead, never Hilan's."""
        when = datetime(2026, 9, 22, 18, 0)
        ours = analyse([parse_month(september())], today=when.date(), now=when).month.worked
        hilan = ours.total_seconds() / 3600 + 5          # 5 hours more, under today's 9
        legend = f"<p>שעות בפועל: {hilan:.2f} | יצרניות: {hilan:.2f}</p>"
        html = september(html_edit=lambda h: h.replace("<span>שעות בפועל 0.00</span>", legend))
        a = analyse([parse_month(html)], today=when.date(), now=when)
        assert a.reconciliation is not None and a.reconciliation.gap == -timedelta(hours=5)

    def test_our_side_ahead_by_no_more_than_today_is_left_alone(self):
        when = datetime(2026, 9, 22, 18, 0)
        ours = analyse([parse_month(september())], today=when.date(), now=when).month.worked
        hilan = ours.total_seconds() / 3600 - 5
        legend = f"<p>שעות בפועל: {hilan:.2f} | יצרניות: {hilan:.2f}</p>"
        html = september(html_edit=lambda h: h.replace("<span>שעות בפועל 0.00</span>", legend))
        assert analyse([parse_month(html)], today=when.date(), now=when).reconciliation is None


class TestASuppliedEntryBeforeANightRow:
    def test_is_not_counted_twice(self):
        day = {22: ([("23:00", "02:00")], [_row("23:00", "02:00", "03:00")])}
        f = at(september(day), datetime(2026, 9, 22, 23, 30), assumed_entry=time(22, 0)).forecast
        assert not f.entry_assumed


class TestAClockOutFilledInByHand:
    def test_is_not_waiting_to_sync(self):
        day = {22: ([("08:00", BLANK)], [_row("08:00", "17:00", "09:00")])}
        a = at(september(day), datetime(2026, 9, 22, 18, 0))
        assert not any(i.kind is IssueKind.PENDING_SYNC and i.date == date(2026, 9, 22) for i in a.issues)


# -- links, foreign folders, odd markup, rows with only a total --------------

class TestAConfigFolderOfSomeoneElse:
    @posix_modes
    def test_is_refused(self, private_config, monkeypatch):
        monkeypatch.setattr(os, "getuid", lambda: os.stat(private_config).st_uid + 1)
        with pytest.raises(PermissionError, match="another user"):
            config.ensure_private_dir(private_config)


class TestALogThatIsALink:
    @posix_modes
    def test_is_not_written_through(self, private_config, tmp_path):
        target = tmp_path / "somebody-elses-file"
        target.write_text("untouched\n")
        (private_config / "hilan.log").symlink_to(target)
        hlog.reset()
        hlog.setup(argv=["hilan"])
        hlog.logger().info("a line")
        assert target.read_text() == "untouched\n"


class TestSavedPagesAgain:
    @pytest.mark.parametrize("tag", [
        '<input type="hidden" name="__VIEWSTATE" value=SECRETSTATE>',
        '<input type="hidden" name="&#x5f;&#x5f;VIEWSTATE" value="SECRETSTATE">',
        "<input type=hidden name='__EVENTVALIDATION' value='SECRETSTATE'>",
    ], ids=["unquoted", "entity-name", "single-quotes"])
    def test_are_scrubbed(self, tag):
        assert "SECRETSTATE" not in redact(tag)


class TestCookieExpiryPerDomain:
    def test_each_cookie_keeps_its_own(self, private_config):
        later, much_later = int(clock.time()) + 600, int(clock.time()) + 7200
        (private_config / "cookies.json").write_text(json.dumps([
            {"name": "TS", "value": "a", "domain": ".hilan.co.il", "path": "/", "expires": later},
            {"name": "TS", "value": "b", "domain": "example.net.hilan.co.il", "path": "/", "expires": much_later},
        ]))
        c = HilanClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert {k.domain: k.expires for k in c._http.cookies.jar} == {
            ".hilan.co.il": later, "example.net.hilan.co.il": much_later}
        c._http.close()


class TestARowWithATotalAndNoExit:
    DAY = {22: ([("08:00", BLANK)], [_row("08:00", "", "04:00")])}

    def test_is_not_running(self):
        f = at(september(self.DAY), datetime(2026, 9, 22, 15, 0)).forecast
        assert f.entry is None and f.worked_so_far == timedelta(hours=4)

    def test_is_not_open_and_covers_the_clock_out(self):
        a = at(september({15: self.DAY[22]}), datetime(2026, 9, 22, 12, 0))
        kinds = {i.kind for i in a.issues if i.date == date(2026, 9, 15)}
        assert IssueKind.OPEN_DAY not in kinds and IssueKind.MISSING_CLOCK_OUT not in kinds

    @needs_node
    def test_the_widget_agrees(self, tmp_path):
        same_in_the_widget(tmp_path, september(self.DAY), datetime(2026, 9, 22, 15, 0))


class TestHoursWithoutTimesOnToday:
    def test_are_shown_as_a_session(self):
        import io as _io
        from rich.console import Console
        from hilan.render import render_summary

        day = {22: ([(BLANK, BLANK)], [_row("", "", "04:00")])}
        out = _io.StringIO()
        render_summary(at(september(day), datetime(2026, 9, 22, 15, 0)),
                       Console(file=out, width=120, color_system=None))
        assert "hours   4.00" in out.getvalue() and "nothing recorded yet" not in out.getvalue()


class TestLoginAnswersThatAreNotAVerdict:
    """Decided: a firewall 403 or a page that is not JSON never judged the password."""

    def answering(self, status, **kwargs):
        def handler(request):
            if request.url.path.endswith("LoginRequest"):
                return httpx.Response(status, **kwargs)
            return httpx.Response(200, text='"OrgId":"1000"')
        return httpx.MockTransport(handler)

    def test_a_403_on_the_post_is_the_firewall(self):
        from hilan.client import Blocked, Rejected

        with HilanClient(transport=self.answering(403)) as c:
            with pytest.raises(Blocked) as raised:
                c.login(config.Credentials(username="1", password="x"))
        assert not isinstance(raised.value, Rejected)

    def test_a_page_that_is_not_json_is_a_failure_not_a_refusal(self):
        from hilan.client import LoginFailed, Rejected

        with HilanClient(transport=self.answering(200, text="<html>maintenance")) as c:
            with pytest.raises(LoginFailed) as raised:
                c.login(config.Credentials(username="1", password="x"))
        assert not isinstance(raised.value, Rejected)


class TestTheCommandLineSaysWhatWentWrong:
    def test_a_login_that_fails_in_another_way(self, monkeypatch):
        from hilan.client import LoginFailed

        class Hilan:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def login(self, *a, **k): raise LoginFailed("login endpoint did not return JSON")

        monkeypatch.setattr("hilan.cli.HilanClient", Hilan)
        result = CliRunner().invoke(main, ["login", "--stdin", "--user", "1"], input="x\n")
        assert result.exit_code != 0 and "did not return JSON" in result.output
        assert "Traceback" not in result.output
        assert config.refused_since() is None

    def test_a_file_that_cannot_be_read(self, tmp_path):
        result = CliRunner().invoke(main, ["--from-file", str(tmp_path / "missing.html")])
        assert result.exit_code != 0 and "Traceback" not in result.output
