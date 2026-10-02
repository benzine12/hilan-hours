# -*- coding: utf-8 -*-
"""Credentials in a .env file, for a machine with no Keychain.

It is plain text at 0600. That is weaker than the Keychain and is said plainly
rather than dressed up; the Keychain is used wherever there is one.
"""
import os
from pathlib import Path

import pytest

from conftest import posix_modes
from hilan import config


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "ENV_FILE", path)
    monkeypatch.delenv("HILAN_ENV", raising=False)
    monkeypatch.delenv("HILAN_PASSWORD", raising=False)
    monkeypatch.delenv("HILAN_USER", raising=False)
    return path


class TestReadingIt:
    def test_a_password_is_found(self, env_file):
        env_file.write_text("HILAN_USER=12345\nHILAN_PASSWORD=hunter2\n")
        assert config.read_env() == {"HILAN_USER": "12345", "HILAN_PASSWORD": "hunter2"}

    def test_blank_lines_and_comments_are_ignored(self, env_file):
        env_file.write_text("# mine\n\nHILAN_USER=12345\n\n  # another\n")
        assert config.read_env() == {"HILAN_USER": "12345"}

    def test_quotes_come_off(self, env_file):
        env_file.write_text("HILAN_PASSWORD='not-a-real-password!'\n")
        assert config.read_env()["HILAN_PASSWORD"] == "not-a-real-password!"

    def test_a_value_may_contain_equals(self, env_file):
        env_file.write_text("HILAN_PASSWORD=a=b=c\n")
        assert config.read_env()["HILAN_PASSWORD"] == "a=b=c"

    def test_trailing_spaces_are_kept_inside_quotes(self, env_file):
        env_file.write_text('HILAN_PASSWORD="two  spaces "\n')
        assert config.read_env()["HILAN_PASSWORD"] == "two  spaces "

    def test_a_missing_file_reads_as_nothing(self, env_file):
        assert config.read_env() == {}

    def test_a_broken_line_does_not_lose_the_rest(self, env_file):
        env_file.write_text("nonsense\nHILAN_USER=12345\n")
        assert config.read_env() == {"HILAN_USER": "12345"}


class TestWhereItLooks:
    def test_an_explicit_path_wins(self, tmp_path, monkeypatch):
        elsewhere = tmp_path / "somewhere.env"
        elsewhere.write_text("HILAN_USER=99999\n")
        monkeypatch.setenv("HILAN_ENV", str(elsewhere))
        assert config.read_env()["HILAN_USER"] == "99999"


class TestUsingIt:
    def test_credentials_come_from_the_file(self, env_file, monkeypatch):
        env_file.write_text("HILAN_USER=12345\nHILAN_PASSWORD=hunter2\n")
        monkeypatch.setattr(config, "_keyring", lambda: None)
        creds = config.resolve_credentials(prompt=False)
        assert (creds.username, creds.password) == ("12345", "hunter2")

    def test_the_real_environment_still_wins(self, env_file, monkeypatch):
        """An explicit export beats a file you forgot you wrote."""
        env_file.write_text("HILAN_PASSWORD=from-file\n")
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        monkeypatch.setattr(config, "_keyring", lambda: None)
        monkeypatch.setenv("HILAN_PASSWORD", "from-export")
        assert config.resolve_credentials(prompt=False).password == "from-export"


class TestWritingIt:
    def test_login_can_store_both(self, env_file, monkeypatch):
        monkeypatch.setattr(config, "_keyring", lambda: None)
        config.password_set("12345", "hunter2")
        text = env_file.read_text()
        assert "HILAN_USER=12345" in text
        assert "HILAN_PASSWORD=hunter2" in text

    @posix_modes
    def test_the_file_is_private(self, env_file, monkeypatch):
        monkeypatch.setattr(config, "_keyring", lambda: None)
        config.password_set("12345", "hunter2")
        assert oct(env_file.stat().st_mode)[-3:] == "600"

    def test_rewriting_keeps_other_keys(self, env_file, monkeypatch):
        env_file.write_text("SOMETHING_ELSE=keep\nHILAN_PASSWORD=old\n")
        monkeypatch.setattr(config, "_keyring", lambda: None)
        config.password_set("12345", "new")
        text = env_file.read_text()
        assert "SOMETHING_ELSE=keep" in text
        assert "HILAN_PASSWORD=new" in text
        assert "old" not in text
