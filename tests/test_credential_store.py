# -*- coding: utf-8 -*-
"""Where the password lives.

In the system's password store wherever there is one, through keyring: the
Keychain on macOS, Credential Manager on Windows, Secret Service (GNOME Keyring,
KWallet) on Linux. A machine without one — a server with no desktop session —
falls back to a file in the config directory, readable only by the owner.

That is weaker than a real store and is said out loud rather than glossed. The
store is still used wherever it exists.
"""
import sys

import keyring
import pytest
from keyring.errors import KeyringError, PasswordSetError

from conftest import MemoryKeyring, posix_modes
from hilan import config


class Refusing(MemoryKeyring):
    """A store that is there but will not cooperate — locked, or access denied."""

    def __init__(self, message="refused"):
        super().__init__()
        self.message = message

    def get_password(self, service, username):
        raise KeyringError(self.message)

    def set_password(self, service, username, password):
        raise PasswordSetError(self.message)


class TestWithAPasswordStore:
    def test_the_password_goes_to_the_store(self, memory_keyring, private_config):
        config.password_set("12345", "hunter2")
        assert memory_keyring.store == {("hilan-hours", "12345"): "hunter2"}
        assert not (private_config / ".env").exists()

    def test_it_is_read_back(self, memory_keyring):
        config.password_set("12345", "hunter2")
        assert config.password_get("12345") == "hunter2"

    def test_another_user_gets_nothing(self, memory_keyring):
        config.password_set("12345", "hunter2")
        assert config.password_get("99999") is None

    def test_nothing_stored_reads_as_nothing(self, memory_keyring):
        assert config.password_get("12345") is None

    def test_it_says_where_the_password_went(self, memory_keyring, private_config):
        where = config.password_set("12345", "hunter2")
        assert where == config.store_name()
        assert ".env" not in where

    def test_the_service_name_never_changes(self):
        """Renaming the service would orphan every password already stored."""
        assert config.KEYRING_SERVICE == "hilan-hours"

    def test_a_password_in_the_file_is_still_found(self, memory_keyring, private_config):
        """A store that refused a write once will have left the password there."""
        (private_config / ".env").write_text(
            "HILAN_USER=12345\nHILAN_PASSWORD=from-file\n", encoding="utf-8")
        assert config.password_get("12345") == "from-file"

    def test_the_store_wins_over_the_file(self, memory_keyring, private_config):
        (private_config / ".env").write_text(
            "HILAN_USER=12345\nHILAN_PASSWORD=from-file\n", encoding="utf-8")
        config.password_set("12345", "from-store")
        assert config.password_get("12345") == "from-store"


class TestWithoutOne:
    def test_the_password_round_trips_through_a_file(self, no_keyring):
        config.password_set("12345", "hunter2")
        assert config.password_get("12345") == "hunter2"

    @posix_modes
    def test_the_file_is_private(self, no_keyring, private_config):
        config.password_set("12345", "hunter2")
        assert oct((private_config / ".env").stat().st_mode)[-3:] == "600"

    def test_another_user_gets_nothing(self, no_keyring):
        config.password_set("12345", "hunter2")
        assert config.password_get("99999") is None

    def test_a_garbled_file_is_not_fatal(self, no_keyring, private_config):
        (private_config / ".env").write_text("{ not json", encoding="utf-8")
        assert config.password_get("12345") is None

    def test_a_file_that_cannot_be_read_is_not_fatal(self, no_keyring, private_config):
        # A directory where the file should be: opening it fails on every system.
        (private_config / ".env").mkdir()
        assert config.password_get("12345") is None

    def test_nothing_stored_reads_as_nothing(self, no_keyring):
        assert config.password_get("12345") is None

    def test_it_says_the_file(self, no_keyring):
        assert config.password_set("12345", "hunter2").endswith(".env")
        assert config.store_name().endswith(".env")

    def test_storing_directly_is_refused(self, no_keyring):
        with pytest.raises(config.StoreFailed):
            config.keyring_set("12345", "hunter2")


class TestAStoreThatWillNotCooperate:
    @pytest.fixture
    def refusing(self):
        keyring.set_keyring(Refusing())

    def test_a_failed_read_is_nothing_stored(self, refusing):
        assert config.password_get("12345") is None

    def test_a_failed_read_still_finds_the_file(self, refusing, private_config):
        (private_config / ".env").write_text(
            "HILAN_USER=12345\nHILAN_PASSWORD=from-file\n", encoding="utf-8")
        assert config.password_get("12345") == "from-file"

    def test_a_failed_write_falls_back_to_the_file(self, refusing, private_config):
        """A login that worked must not be lost because the store refused."""
        where = config.password_set("12345", "hunter2")
        assert where.endswith(".env")
        assert config.password_get("12345") == "hunter2"

    def test_the_error_never_carries_the_password(self):
        """Even a backend that puts the password in its own message."""
        keyring.set_keyring(Refusing(message="could not store not-a-real-password!"))
        with pytest.raises(config.StoreFailed) as caught:
            config.keyring_set("12345", "not-a-real-password!")
        assert "not-a-real-password!" not in str(caught.value)
        assert "PasswordSetError" in str(caught.value)


class TestTheStoreSaysWhichItIs:
    """Named after the backend keyring chose, not after the platform."""

    @pytest.mark.parametrize("module, expected", [
        ("keyring.backends.macOS", "the macOS Keychain"),
        ("keyring.backends.Windows", "Windows Credential Manager"),
    ])
    def test_the_usual_names(self, module, expected):
        backend = type("Keyring", (MemoryKeyring,), {"__module__": module})()
        keyring.set_keyring(backend)
        assert config.store_name() == expected

    def test_any_other_backend_names_itself(self, memory_keyring):
        assert config.store_name() == f"the system keyring ({memory_keyring.name})"

    def test_a_store_configured_to_keep_nothing_is_no_store(self, private_config):
        """keyring's null backend accepts a password and keeps nothing."""
        from keyring.backends import null
        keyring.set_keyring(null.Keyring())
        assert config.password_set("12345", "hunter2").endswith(".env")
        assert config.password_get("12345") == "hunter2"


class TestLoggingInWithoutAPrompt:
    """A script has nobody at the keyboard, so the password can come from the
    environment instead of a prompt."""

    def test_the_environment_is_used_when_set(self, monkeypatch):
        monkeypatch.setenv("HILAN_PASSWORD", "hunter2")
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        assert config.resolve_credentials(prompt=False).password == "hunter2"

    def test_it_is_preferred_over_prompting(self, monkeypatch):
        """Nothing may block on a prompt that cannot be answered."""
        monkeypatch.setenv("HILAN_PASSWORD", "hunter2")
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        monkeypatch.setattr(
            "getpass.getpass",
            lambda *a, **k: pytest.fail("getpass was called with a password to hand"),
        )
        assert config.resolve_credentials().password == "hunter2"

    def test_an_explicit_export_beats_the_stored_one(self, monkeypatch):
        """Setting a variable for one command means it to win."""
        config.password_set("12345", "stored")
        monkeypatch.setenv("HILAN_PASSWORD", "from-env")
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        assert config.resolve_credentials().password == "from-env"

    def test_the_stored_one_is_used_when_nothing_is_exported(self, monkeypatch):
        config.password_set("12345", "stored")
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        assert config.resolve_credentials().password == "stored"

    def test_nothing_anywhere_and_no_terminal_says_what_to_run(self, monkeypatch):
        monkeypatch.setattr(config, "load_username", lambda: "12345")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        with pytest.raises(config.CredentialsUnavailable, match="hilan login"):
            config.resolve_credentials()


class TestTheRealStoreIsNeverTouched:
    def test_tests_run_against_a_store_of_their_own(self, memory_keyring):
        assert keyring.get_keyring() is memory_keyring
