# -*- coding: utf-8 -*-
"""Which Hilan site to talk to is configuration, and only Hilan's sites qualify.

Every company has its own address (https://<company>.net.hilan.co.il), so it
cannot live in the code. It is also where the password is sent, which is why
anything that is not plainly one of Hilan's own https hosts is refused rather
than tried.
"""
import json

import httpx
import pytest
from click.testing import CliRunner

from conftest import posix_modes
from hilan import config
from hilan.cli import main
from hilan.client import HilanClient, ReadOnlyViolation, build_client


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("HILAN_ENV", raising=False)
    monkeypatch.delenv("HILAN_URL", raising=False)
    return tmp_path


class TestNormalising:
    @pytest.mark.parametrize("typed", [
        "example.net.hilan.co.il",
        "https://example.net.hilan.co.il",
        "https://example.net.hilan.co.il/",
        "https://example.net.hilan.co.il/login",
        "https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx?isOnSelf=true",
        "  https://example.net.hilan.co.il  ",
        "HTTPS://EXAMPLE.NET.HILAN.CO.IL",
        "https://example.net.hilan.co.il:443",
        "example.hilan.co.il",
    ])
    def test_the_ways_people_write_it(self, typed):
        expected = "https://example.hilan.co.il" if typed == "example.hilan.co.il" \
            else "https://example.net.hilan.co.il"
        assert config.normalise_url(typed) == expected

    @pytest.mark.parametrize("typed", [
        "http://example.net.hilan.co.il",              # the password in clear text
        "https://example.com",
        "https://example.net.hilan.co.il.evil.com",
        "https://evilhilan.co.il",
        "https://evil.com/.hilan.co.il",
        "https://evil.com#.hilan.co.il",
        "https://user@evil.com",
        "https://hilan.co.il",                         # Hilan itself, not a company's site
        "ftp://example.net.hilan.co.il",
        "https://exa mple.net.hilan.co.il",
        "https://[broken",
        "",
        "   ",
        None,
    ])
    def test_anything_else_is_refused(self, typed):
        with pytest.raises(config.NotConfigured):
            config.normalise_url(typed)

    def test_the_refusal_shows_what_is_expected(self):
        with pytest.raises(config.NotConfigured, match="yourcompany.net.hilan.co.il"):
            config.normalise_url("https://example.com")


class TestWhereTheAddressComesFrom:
    def test_nothing_configured(self, config_dir):
        assert config.load_url() is None
        with pytest.raises(config.NotConfigured, match="hilan login"):
            config.base_url()

    def test_the_environment(self, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "one.net.hilan.co.il")
        assert config.base_url() == "https://one.net.hilan.co.il"

    def test_the_env_file(self, config_dir):
        (config_dir / ".env").write_text("HILAN_URL=https://two.net.hilan.co.il/\n")
        assert config.base_url() == "https://two.net.hilan.co.il"

    def test_config_json(self, config_dir):
        (config_dir / "config.json").write_text(json.dumps({"url": "https://three.net.hilan.co.il"}))
        assert config.base_url() == "https://three.net.hilan.co.il"

    def test_the_environment_wins_over_the_files(self, config_dir, monkeypatch):
        (config_dir / ".env").write_text("HILAN_URL=two.net.hilan.co.il\n")
        (config_dir / "config.json").write_text(json.dumps({"url": "three.net.hilan.co.il"}))
        monkeypatch.setenv("HILAN_URL", "one.net.hilan.co.il")
        assert config.base_url() == "https://one.net.hilan.co.il"

    def test_the_env_file_wins_over_config_json(self, config_dir):
        (config_dir / ".env").write_text("HILAN_URL=two.net.hilan.co.il\n")
        (config_dir / "config.json").write_text(json.dumps({"url": "three.net.hilan.co.il"}))
        assert config.base_url() == "https://two.net.hilan.co.il"

    def test_a_configured_address_that_is_not_hilan_is_refused(self, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://example.com")
        with pytest.raises(config.NotConfigured):
            config.base_url()

    @pytest.mark.parametrize("content", ["not json", "[1, 2]", '"a string"', ""])
    def test_a_broken_config_json_is_no_address(self, config_dir, content):
        (config_dir / "config.json").write_text(content)
        assert config.load_url() is None
        assert config.load_username() is None


class TestSaving:
    def test_it_is_stored_normalised(self, config_dir):
        config.save_url("example.net.hilan.co.il/login")
        stored = json.loads((config_dir / "config.json").read_text())
        assert stored["url"] == "https://example.net.hilan.co.il"

    def test_the_username_is_kept(self, config_dir):
        config.save_username("12345")
        config.save_url("example.net.hilan.co.il")
        stored = json.loads((config_dir / "config.json").read_text())
        assert stored == {"username": "12345", "url": "https://example.net.hilan.co.il"}

    @posix_modes
    def test_the_file_is_private(self, config_dir):
        config.save_url("example.net.hilan.co.il")
        assert (config_dir / "config.json").stat().st_mode & 0o777 == 0o600

    def test_a_bad_address_is_not_stored(self, config_dir):
        with pytest.raises(config.NotConfigured):
            config.save_url("https://example.com")
        assert not (config_dir / "config.json").exists()


class TestTheClient:
    def _ok(self):
        return httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))

    def test_it_is_bound_to_the_configured_site(self, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://mine.net.hilan.co.il")
        with build_client(transport=self._ok()) as client:
            assert str(client.base_url).rstrip("/") == "https://mine.net.hilan.co.il"
            assert client.get("/login").status_code == 200

    def test_another_hilan_site_is_refused(self, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://mine.net.hilan.co.il")
        with build_client(transport=self._ok()) as client:
            with pytest.raises(ReadOnlyViolation, match="foreign host"):
                client.get("https://theirs.net.hilan.co.il/login")

    def test_an_explicit_address_wins(self, config_dir):
        with build_client(transport=self._ok(), base="https://given.net.hilan.co.il") as client:
            assert client.base_url.host == "given.net.hilan.co.il"

    def test_an_explicit_address_is_held_to_the_same_rule(self, config_dir):
        with pytest.raises(config.NotConfigured):
            build_client(transport=self._ok(), base="https://example.com")

    def test_no_address_fails_when_a_client_is_made(self, config_dir):
        with pytest.raises(config.NotConfigured):
            build_client(transport=self._ok())

    def test_importing_needs_no_address(self, tmp_path):
        # In a fresh interpreter: `hilan --help` must work before anything is set up.
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k not in ("HILAN_URL", "HILAN_ENV")}
        env["HILAN_HOME"] = str(tmp_path)
        done = subprocess.run(
            [sys.executable, "-c", "import hilan.client, hilan.cli"],
            env=env, capture_output=True, text=True, encoding="utf-8",
            cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        )
        assert done.returncode == 0, done.stderr

    def test_stored_cookies_without_a_domain_go_to_the_configured_site(self, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://mine.net.hilan.co.il")
        monkeypatch.setattr("hilan.client.COOKIE_FILE", config_dir / "cookies.json")
        (config_dir / "cookies.json").write_text(json.dumps([{"name": "s", "value": "v"}]))
        client = HilanClient(transport=self._ok())
        try:
            assert [c.domain for c in client._http.cookies.jar] == ["mine.net.hilan.co.il"]
        finally:
            client._http.close()


class TestTheCommandLine:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def fake_login(self, monkeypatch, config_dir):
        seen = {}

        def init(self, transport=None, base=None):
            seen["base"] = base

        def login(self, creds, verification_code=None):
            seen["user"] = creds.username

        monkeypatch.setattr(HilanClient, "__init__", init)
        monkeypatch.setattr(HilanClient, "login", login)
        monkeypatch.setattr(HilanClient, "__enter__", lambda self: self)
        monkeypatch.setattr(HilanClient, "__exit__", lambda self, *a: False)
        monkeypatch.setattr(config, "_keyring", lambda: None)
        monkeypatch.setattr("hilan.history.HISTORY_FILE", config_dir / "history.json")
        monkeypatch.setenv("HILAN_PASSWORD", "hunter2")
        return seen

    def test_a_run_with_no_address_says_what_to_do(self, runner, config_dir, monkeypatch):
        monkeypatch.setattr("hilan.history.HISTORY_FILE", config_dir / "history.json")
        result = runner.invoke(main, ["--today", "2026-09-22"])
        assert result.exit_code != 0
        assert "hilan login" in result.output
        assert "Traceback" not in result.output

    def test_login_asks_for_the_address_and_keeps_it(self, runner, fake_login, config_dir):
        result = runner.invoke(main, ["login"], input="example.net.hilan.co.il/login\n12345\n")
        assert result.exit_code == 0, result.output
        assert fake_login["base"] == "https://example.net.hilan.co.il"
        stored = json.loads((config_dir / "config.json").read_text())
        assert stored["url"] == "https://example.net.hilan.co.il"
        assert config.base_url() == "https://example.net.hilan.co.il"

    def test_login_refuses_an_address_that_is_not_hilan(self, runner, fake_login, config_dir):
        result = runner.invoke(main, ["login"], input="https://example.com\n12345\n")
        assert result.exit_code != 0
        assert "not a Hilan address" in result.output
        assert "user" not in fake_login                 # the password was never sent
        assert not (config_dir / "config.json").exists()

    def test_login_does_not_ask_when_the_address_is_set(self, runner, fake_login, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://example.net.hilan.co.il")
        result = runner.invoke(main, ["login"], input="12345\n")
        assert result.exit_code == 0, result.output
        assert fake_login["base"] == "https://example.net.hilan.co.il"
        # An address given for one run stays out of the saved settings.
        assert "url" not in json.loads((config_dir / "config.json").read_text())

    def test_login_with_stdin_cannot_ask(self, runner, fake_login, config_dir):
        result = runner.invoke(main, ["login", "--stdin"], input="hunter2\n")
        assert result.exit_code != 0
        assert "HILAN_URL" in result.output
        assert "user" not in fake_login

    def test_login_with_a_bad_configured_address_says_so(self, runner, fake_login, config_dir, monkeypatch):
        monkeypatch.setenv("HILAN_URL", "https://example.com")
        result = runner.invoke(main, ["login"], input="example.net.hilan.co.il\n12345\n")
        assert result.exit_code != 0
        assert "not a Hilan address" in result.output
        assert "user" not in fake_login

    def test_login_with_no_input_at_all(self, runner, fake_login, config_dir):
        result = runner.invoke(main, ["login"], input="")
        assert result.exit_code != 0
        assert "hilan login" in result.output or "HILAN_URL" in result.output
        assert "Traceback" not in result.output
