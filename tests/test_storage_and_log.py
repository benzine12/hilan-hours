# -*- coding: utf-8 -*-
"""Where the password and settings are kept, what reaches the log, and the CLI
around them: each case is one way a person or a server can surprise the tool."""
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import keyring
import pytest
from click.testing import CliRunner

from conftest import MemoryKeyring, posix_modes
from hilan import config, history
from hilan import log as hlog
from hilan.cli import main
from hilan.client import HilanClient, ReadOnlyViolation, build_client, redact

LOGIN = "/HilanCenter/Public/api/LoginApi/LoginRequest"


def fake_hilan(answer=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path == LOGIN:
            return answer(request) if answer else httpx.Response(200, json={"IsFail": False})
        return httpx.Response(200, text='"OrgId":"1000"')
    return httpx.MockTransport(handler)


@pytest.fixture
def hilan(monkeypatch):
    seen = []
    state = {"answer": None}
    real = HilanClient
    monkeypatch.setattr("hilan.cli.HilanClient",
                        lambda base=None, transport=None: real(transport=fake_hilan(state["answer"], seen), base=base))
    return seen, state


def login(*args, input="s3cret\n"):
    return CliRunner().invoke(main, ["login", "--stdin", "--user", "12345", *args], input=input)


class TestTheEnvFile:
    def write(self, private_config, text, encoding="utf-8"):
        (private_config / ".env").write_bytes(text.encode(encoding))

    @pytest.mark.parametrize("line,password", [
        ("HILAN_PASSWORD=hunter2 # work password", "hunter2"),
        ('HILAN_PASSWORD="hunter2" # x', "hunter2"),
        ("HILAN_PASSWORD='hunter2' # x", "hunter2"),
        ("HILAN_PASSWORD=hun#ter2", "hun#ter2"),              # no space before it: part of it
        ("export HILAN_PASSWORD=hunter2", "hunter2"),
        ("﻿HILAN_PASSWORD=hunter2", "hunter2"),
    ], ids=["comment", "quoted-comment", "single-quoted-comment", "hash-inside", "export", "bom"])
    def test_lines_a_person_writes(self, no_keyring, private_config, line, password):
        self.write(private_config, f"{line}\nHILAN_USER=12345\n")
        assert config.password_get("12345") == password

    def test_a_bom_or_export_does_not_hide_whose_file_it_is(self, no_keyring, private_config):
        self.write(private_config, "﻿HILAN_USER=111\nHILAN_PASSWORD=p111\n")
        assert config.password_get("222") is None
        self.write(private_config, "export HILAN_USER=111\nHILAN_PASSWORD=p111\n")
        assert config.password_get("222") is None

    @pytest.mark.parametrize("password", ["s3\x85cret", "a b", "a\x0bb", "x\x1cy", "ab\nHILAN_USER=999"])
    def test_characters_that_split_lines_elsewhere(self, no_keyring, password):
        config.password_set("12345", password)
        assert config.password_get("12345") == password
        assert config.resolve_credentials(prompt=False).username == "12345"

    def test_a_file_that_is_not_utf8_says_so(self, no_keyring, private_config):
        self.write(private_config, "HILAN_USER=12345\nHILAN_PASSWORD=p\xe4ss\n", "cp1252")
        with pytest.raises(config.CredentialsUnavailable, match="not UTF-8"):
            config.password_get("12345")

    def test_hilan_env_with_a_tilde(self, no_keyring, monkeypatch, tmp_path):
        # ~ is HOME on macOS and Linux, USERPROFILE on Windows.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HILAN_ENV", "~/hilan.env")
        config.password_set("12345", "x")
        assert (tmp_path / "hilan.env").exists()


class TestThePasswordStore:
    def test_a_new_password_the_store_will_not_take_does_not_lose_to_the_old_one(self, monkeypatch, private_config):
        store = MemoryKeyring()
        keyring.set_keyring(store)
        config.password_set("12345", "OLD-pw")
        monkeypatch.setattr(store, "set_password", lambda *a: (_ for _ in ()).throw(RuntimeError("locked")))
        config.password_set("12345", "NEW-pw")
        assert config.password_get("12345") == "NEW-pw"

    def test_a_plain_text_copy_goes_once_the_store_has_it(self, no_keyring, private_config):
        config.password_set("12345", "pw-A")                      # no store: the file
        assert "pw-A" in (private_config / ".env").read_text()
        keyring.set_keyring(MemoryKeyring())
        config.password_set("12345", "pw-B")
        assert "pw-A" not in (private_config / ".env").read_text()
        assert config.password_get("12345") == "pw-B"

    def test_other_lines_of_the_file_stay(self, no_keyring, private_config):
        (private_config / ".env").write_text("HILAN_URL=https://example.net.hilan.co.il\n# mine\n")
        config.password_set("12345", "pw")
        keyring.set_keyring(MemoryKeyring())
        config.password_set("12345", "pw2")
        text = (private_config / ".env").read_text()
        assert "HILAN_URL=https://example.net.hilan.co.il" in text and "# mine" in text


class TestSettingsEditedByHand:
    @pytest.mark.parametrize("content,username", [
        ('{"username": 12345}', "12345"),
        ('﻿{"username": "12345"}', "12345"),
        ('{"username": " 12345 "}', "12345"),
        ('{"username": true}', None),
    ])
    def test_are_read_sensibly(self, private_config, content, username):
        (private_config / "config.json").write_text(content, encoding="utf-8")
        assert config.load_username() == username

    def test_a_number_as_the_address_is_no_address(self, private_config, monkeypatch):
        monkeypatch.delenv("HILAN_URL", raising=False)
        (private_config / "config.json").write_text('{"url": 5}')
        result = CliRunner().invoke(main, [])
        assert "Traceback" not in result.output and result.exit_code != 0


class TestLoggingIn:
    def test_an_empty_password_is_not_sent(self, hilan):
        seen, _ = hilan
        result = login(input="\n")
        assert result.exit_code != 0 and "no password" in result.output
        assert not [r for r in seen if r.url.path == LOGIN]

    def test_spaces_around_the_number_are_not_part_of_it(self, hilan):
        seen, _ = hilan
        CliRunner().invoke(main, ["login", "--stdin", "--user", " 12345 "], input="s3cret\n")
        body = next(r for r in seen if r.url.path == LOGIN).content.decode()
        assert "username=12345&" in body

    def test_the_stop_is_lifted_only_once_the_password_is_saved(self, hilan, monkeypatch):
        config.mark_refused()
        monkeypatch.setattr(config, "password_set", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        assert login().exit_code != 0
        assert config.refused_since() is not None

    def test_hilans_words_do_not_carry_the_password(self, hilan):
        _, state = hilan
        state["answer"] = lambda r: httpx.Response(200, json={"IsFail": True, "ErrorMessage": "wrong: s3cret"})
        result = login()
        assert "s3cret" not in result.output and "<redacted>" in result.output

    def test_a_character_that_cannot_be_sent_is_not_shown(self, hilan, monkeypatch):
        monkeypatch.setenv("HILAN_PASSWORD", "p\udce4ss")
        result = CliRunner().invoke(main, ["login", "--user", "12345"])
        assert result.exit_code != 0 and "cannot be sent" in result.output
        assert "\\udce4" not in result.output and "Traceback" not in result.output


class TestAVerificationCodeTypedWrong:
    def test_leaves_the_stop_in_place(self, monkeypatch):
        from hilan.client import VerificationRequired
        import hilan.cli as cli

        class Hilan:
            def is_authenticated(self): return False
            def login(self, creds, verification_code=None):
                raise VerificationRequired("code sent")

        monkeypatch.setattr(config, "resolve_credentials",
                            lambda: config.Credentials(username="1", password="x"))
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(cli.click, "prompt", lambda *a, **k: "000000")
        with pytest.raises(VerificationRequired):
            cli._authenticate(Hilan())
        assert "verification" in (config.refused_since() or "")


class TestTheLog:
    def test_a_traceback_is_redacted(self, private_config):
        hlog.setup(argv=["hilan"])
        try:
            raise httpx.HTTPError("failed for url 'https://x/login?password=hunter2&__VIEWSTATE=abcdef'")
        except httpx.HTTPError:
            hlog.failure("checking")
        text = (private_config / "hilan.log").read_text(encoding="utf-8")
        assert "hunter2" not in text and "abcdef" not in text and "failed: checking" in text

    @pytest.mark.parametrize("url", [
        "https://x/y?pass%77ord=hunter2", "https://x/y?ReturnUrl=%2Flogin%3Fpassword%3Dhunter2",
        "https://x/y?sid=hunter2",
    ])
    def test_a_url_is_logged_by_its_names(self, private_config, url):
        hlog.setup(argv=["hilan"])
        hlog.request_done("GET", url, 200, 0.1)
        text = (private_config / "hilan.log").read_text(encoding="utf-8")
        assert "hunter2" not in text and "https://x/y?" in text


class TestSavedPagesWithTheParser:
    @pytest.mark.parametrize("page", [
        '<input name=__VIEWSTATE value="SECRET">',
        '<input data-name="x" name="__VIEWSTATE" value="SECRET">',
        '<input title="name=__x" name="__VIEWSTATE" value="SECRET">',
        '<input name="__VIEWSTATE" value="a" value="SECRET">',
        '<input title="a>b" name="__VIEWSTATE" value="SECRET">',
        '<input name="__VIEWSTATE1" value="SECRET">',
        '<input name="verificationCode" value="SECRET">',
        '<meta name="H-XSRF-Token" content="SECRET">',
    ], ids=["unquoted-name", "data-name", "name-in-a-value", "repeated-value", "gt-in-a-value",
            "split-viewstate", "verification", "meta"])
    def test_are_scrubbed_as_read(self, page):
        assert "SECRET" not in redact(page)


class TestTheGuardAgain:
    @pytest.fixture
    def client(self):
        with build_client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
            yield c

    @pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "PROPFIND"])
    def test_a_method_with_a_read_body(self, client, method):
        with pytest.raises(ReadOnlyViolation, match="never needed"):
            client.request(method, "https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx",
                           data={"__calendarSelectedDays": "9740", "ctl00$mp$RefreshSelectedDays": "x"})

    @pytest.mark.parametrize("path", ["/x/Hilannetv2/Attendance/calendarpage.aspx",
                                      "/HilanCenter/Public/api/LoginApi/ChangePassword"])
    def test_paths_that_only_end_or_begin_right(self, client, path):
        with pytest.raises(ReadOnlyViolation, match="outside the read allowlist"):
            client.post("https://example.net.hilan.co.il" + path,
                        data={"__calendarSelectedDays": "9740", "ctl00$mp$RefreshSelectedDays": "x"})

    def test_an_address_with_a_login_in_it(self, client):
        with pytest.raises(ReadOnlyViolation, match="carries a login"):
            client.get("https://12345:pw@example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx")

    def test_a_post_resent_by_a_307_is_refused_not_a_crash(self):
        def handler(request):
            if request.url.path == LOGIN and "again" not in str(request.url):
                return httpx.Response(307, headers={"location": LOGIN + "?again=1"})
            return httpx.Response(200, json={"IsFail": False})
        with build_client(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(ReadOnlyViolation):
                c.post("https://example.net.hilan.co.il" + LOGIN, data={"username": "x", "password": "y"})

    @pytest.mark.parametrize("url", ["https://xn--example-.net.hilan.co.il", "https://xn--a.hilan.co.il"])
    def test_an_address_no_client_can_encode(self, url):
        with pytest.raises(config.NotConfigured):
            config.normalise_url(url)


class TestTheCommandLine:
    def test_month_picks_the_page_among_the_files(self, tmp_path, september_html, august_html):
        (tmp_path / "s.html").write_text(september_html, encoding="utf-8")
        (tmp_path / "a.html").write_text(august_html, encoding="utf-8")
        result = CliRunner().invoke(main, ["--from-file", str(tmp_path / "s.html"), "--from-file",
                                           str(tmp_path / "a.html"), "--month", "2026-08", "--json"])
        assert json.loads(result.output)["month"] == 8

    def test_a_month_no_file_holds_is_said(self, tmp_path, september_html):
        (tmp_path / "s.html").write_text(september_html, encoding="utf-8")
        result = CliRunner().invoke(main, ["--from-file", str(tmp_path / "s.html"), "--month", "2026-07"])
        assert result.exit_code != 0 and "2026-07" in result.output and "Traceback" not in result.output

    def test_history_keeps_hebrew_as_hilan_wrote_it(self, tmp_path, monkeypatch):
        from test_edge_days import BLANK, _row, september

        monkeypatch.delenv("HILAN_BIDI", raising=False)
        page = tmp_path / "s.html"
        page.write_text(september({15: ([(BLANK, BLANK)], [_row("", "", BLANK, "9.00", "השתלמות")])}),
                        encoding="utf-8")
        CliRunner().invoke(main, ["--from-file", str(page)])
        kept = json.dumps(history.load(), ensure_ascii=False)
        assert "השתלמות" in kept and "תומלתשה" not in kept

    def test_a_changed_punch_reads_as_times_not_a_list(self, tmp_path, september_html, monkeypatch):
        page = tmp_path / "s.html"
        page.write_text(september_html, encoding="utf-8")
        CliRunner().invoke(main, ["--from-file", str(page)])
        stored = history.load()
        day = next(iter(stored))
        stored[day]["punches"] = ["07:55-15:00"]
        history.HISTORY_FILE.write_text(json.dumps(stored))
        out = CliRunner().invoke(main, ["--from-file", str(page)]).output
        assert "['" not in out and "07:55-15:00 ->" in out


class TestTheConfigFolder:
    def test_a_relative_xdg_folder_is_ignored(self, monkeypatch):
        monkeypatch.delenv("HILAN_HOME", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
        assert config.pick_config_dir().is_absolute()

    def test_a_relative_hilan_home_is_made_absolute(self, monkeypatch):
        monkeypatch.setenv("HILAN_HOME", "here")
        assert config.pick_config_dir() == Path("here").absolute()

    @posix_modes
    def test_a_link_someone_else_made_is_refused(self, private_config, monkeypatch):
        real_lstat = Path.lstat
        monkeypatch.setattr(Path, "lstat", lambda self: type("S", (), {"st_uid": os.getuid() + 1})()
                            if self == private_config else real_lstat(self))
        with pytest.raises(PermissionError):
            config.ensure_private_dir(private_config)

    def test_a_file_is_on_disk_before_it_replaces_the_old_one(self, private_config, monkeypatch):
        synced = []
        real = os.fsync
        monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
        config.write_private(private_config / "x.json", "{}")
        assert synced
