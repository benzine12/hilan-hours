# -*- coding: utf-8 -*-
"""Where the settings, session, history and log live, on every platform."""
import pytest

from hilan import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A made-up home directory, the way each platform looks it up.

    POSIX reads HOME; Windows reads USERPROFILE and ignores HOME.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in ("HILAN_HOME", "XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(config, "_is_windows", lambda: True)


@pytest.fixture
def posix(monkeypatch):
    monkeypatch.setattr(config, "_is_windows", lambda: False)


class TestOnMacAndLinux:
    def test_the_usual_place(self, home, posix):
        assert config.pick_config_dir() == home / ".config" / "hilan-hours"

    def test_xdg_is_honoured(self, home, posix, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
        assert config.pick_config_dir() == home / "xdg" / "hilan-hours"

    def test_an_empty_xdg_is_ignored(self, home, posix, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert config.pick_config_dir() == home / ".config" / "hilan-hours"


class TestOnWindows:
    def test_appdata(self, home, windows, monkeypatch):
        monkeypatch.setenv("APPDATA", str(home / "Roaming"))
        assert config.pick_config_dir() == home / "Roaming" / "hilan-hours"

    def test_without_appdata_its_usual_place(self, home, windows):
        assert config.pick_config_dir() == home / "AppData" / "Roaming" / "hilan-hours"

    def test_xdg_still_wins_when_set(self, home, windows, monkeypatch):
        monkeypatch.setenv("APPDATA", str(home / "Roaming"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
        assert config.pick_config_dir() == home / "xdg" / "hilan-hours"


class TestTheOverride:
    @pytest.mark.parametrize("platform", ["posix", "windows"])
    def test_it_wins_everywhere(self, home, platform, request, monkeypatch):
        request.getfixturevalue(platform)
        monkeypatch.setenv("APPDATA", str(home / "Roaming"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
        monkeypatch.setenv("HILAN_HOME", str(home / "mine"))
        assert config.pick_config_dir() == home / "mine"

    def test_a_tilde_is_expanded(self, home, monkeypatch):
        monkeypatch.setenv("HILAN_HOME", "~/hh")
        assert config.pick_config_dir() == home / "hh"


class TestNothingIsCreated:
    def test_choosing_it_leaves_no_trace(self, home):
        """Only writing a file makes the directory; importing the tool does not."""
        assert not config.pick_config_dir().exists()


class TestThePlatformCheck:
    @pytest.mark.parametrize("platform, expected", [
        ("win32", True), ("darwin", False), ("linux", False), ("cygwin", False),
    ])
    def test_it_reads_sys_platform(self, monkeypatch, platform, expected):
        monkeypatch.setattr("sys.platform", platform)
        assert config._is_windows() is expected
