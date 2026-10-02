"""Where the tool gets its settings and credentials.

The password is never written into the project. It comes from the environment,
the system's password store (the Keychain on macOS, Credential Manager on
Windows, Secret Service on Linux) or the .env file, and failing those from an
interactive prompt.

Every company has its own Hilan site, so the address is configuration too:
``HILAN_URL`` in the environment or the .env file, or whatever ``hilan login``
saved.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import keyring
from keyring.backends import fail, null

URL_EXAMPLE = "https://yourcompany.net.hilan.co.il"
# The password is sent to this host, so it has to be one of Hilan's own.
_HILAN_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.hilan\.co\.il$")
LOGIN_PATH = "/HilanCenter/Public/api/LoginApi/LoginRequest"
ATTENDANCE_PATH = "/Hilannetv2/Attendance/calendarpage.aspx"
ATTENDANCE_QUERY = {"isOnSelf": "true"}

#: The name the password is stored under in the system's password store.
KEYRING_SERVICE = "hilan-hours"


def _is_windows() -> bool:
    return sys.platform == "win32"


def pick_config_dir() -> Path:
    """Where the settings, the session, the history and the log live.

    HILAN_HOME if set; else $XDG_CONFIG_HOME/hilan-hours; else the platform's
    usual place — %APPDATA%\\hilan-hours on Windows, ~/.config/hilan-hours
    elsewhere. Nothing is created here: whatever writes a file makes the
    directory first, so merely importing the tool leaves no trace.
    """
    override = os.environ.get("HILAN_HOME")
    if override:
        # Made absolute now: a relative one would follow whatever folder the
        # command happens to be run from.
        return Path(override).expanduser().absolute()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    # The XDG spec: a relative value is invalid and to be ignored.
    if xdg and Path(xdg).expanduser().is_absolute():
        return Path(xdg).expanduser() / "hilan-hours"
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "hilan-hours"
    return Path.home() / ".config" / "hilan-hours"


CONFIG_DIR = pick_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
COOKIE_FILE = CONFIG_DIR / "cookies.json"
#: Credentials where there is no password store: plain text, 0600, yours to edit.
ENV_FILE = CONFIG_DIR / ".env"


@dataclass(frozen=True)
class Credentials:
    username: str
    # Kept out of repr(): a Credentials in a traceback or a debug print must
    # not spell the password out.
    password: str = field(repr=False)


def ensure_private_dir(path: Path) -> None:
    """Make the directory if need be, and let nobody else into it.

    One that belongs to someone else — a HILAN_HOME pointed into a shared
    folder another user made first — is refused: whoever owns it can swap the
    files in it for links to anything of ours.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The link itself as well as what it points to: a link someone else made
    # can be pointed elsewhere after this check.
    if hasattr(os, "getuid"):
        uid = os.getuid()
        if path.lstat().st_uid != uid or path.stat().st_uid != uid:
            raise PermissionError(f"{path} belongs to another user; not keeping anything there")
    try:
        path.chmod(0o700)
    except OSError:
        pass


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read — from the first byte, not after.

    Creating with the default mode and tightening it afterwards leaves a moment
    in which anyone on the machine can read the password or the session. The
    text goes to a private temporary file that then replaces the old one, so a
    crash halfway leaves the previous version whole rather than a torn one.
    """
    # A new file of its own, created private (mkstemp: 0600, O_EXCL, and on
    # Windows O_BINARY, so no \r is added): two runs at once never write into
    # each other's, and none is ever opened wider and tightened afterwards.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            # On disk before it replaces the old file: a power cut must leave
            # one whole version, not an empty file where history was.
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows will not replace a file another process has open — a
                # second run reading it. That passes; a lasting refusal does not.
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.1)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _settings() -> dict:
    """config.json, or nothing if it is missing, unreadable or not an object."""
    try:
        # utf-8-sig: a file saved by an editor that adds a byte-order mark.
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    # A hand edit may leave a number where text belongs ("username": 12345);
    # anything that is not text or a number is not a setting.
    return {k: str(v).strip() for k, v in data.items()
            if isinstance(v, (str, int)) and not isinstance(v, bool)}


def _save_setting(key: str, value: str) -> None:
    ensure_private_dir(CONFIG_DIR)
    data = _settings()
    data[key] = value
    write_private(CONFIG_FILE, json.dumps(data, indent=2))


def load_username() -> str | None:
    if env := os.environ.get("HILAN_USER", "").strip():
        return env
    return _settings().get("username") or None


def save_username(username: str) -> None:
    _save_setting("username", username)


class NotConfigured(RuntimeError):
    """There is no Hilan address to talk to, or the one given is not Hilan's."""


def _idna_ok(host: str) -> bool:
    """Whether the host is one an HTTP client can actually encode (xn-- labels)."""
    import httpx

    try:
        return bool(httpx.URL(f"https://{host}/").host)
    except (httpx.InvalidURL, UnicodeError, ValueError):
        return False


def normalise_url(raw: str | None) -> str:
    """``https://<host>`` for a Hilan address written any reasonable way.

    Pasted from a browser it comes with a path (``/login``) and perhaps a
    trailing slash; typed by hand, often with no scheme at all. Only the host
    matters. Plain http is refused rather than quietly upgraded, and so is any
    host outside hilan.co.il: this is where the password goes.
    """
    text = (raw or "").strip()
    if not text:
        raise NotConfigured(f"no Hilan address given — expected something like {URL_EXAMPLE}")
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    except ValueError:                        # e.g. an unclosed "[" in the host
        scheme, host = "", ""
    if scheme != "https" or not _HILAN_HOST.match(host) or not _idna_ok(host):
        raise NotConfigured(
            f"{text!r} is not a Hilan address — expected https:// and a"
            f" hilan.co.il site, like {URL_EXAMPLE}"
        )
    return f"https://{host}"


def load_url() -> str | None:
    """The address as configured: environment, then the .env file, then config.json."""
    return (
        os.environ.get("HILAN_URL")
        or read_env().get("HILAN_URL")
        or _settings().get("url")
    )


def base_url() -> str:
    raw = load_url()
    if not raw:
        raise NotConfigured(
            "no Hilan address configured — run `hilan login`, or set"
            f" HILAN_URL (like {URL_EXAMPLE})"
        )
    return normalise_url(raw)


def save_url(url: str) -> None:
    _save_setting("url", normalise_url(url))


def _keyring():
    """The system's password store, or None where it has none.

    keyring picks the platform's own: the Keychain on macOS, Credential Manager
    on Windows, Secret Service (GNOME Keyring, KWallet) on Linux. A machine with
    no desktop session usually has none, and gets the .env file instead.
    """
    backend = keyring.get_keyring()
    # fail: there is none. null: one configured to store nothing, on purpose.
    return None if isinstance(backend, (fail.Keyring, null.Keyring)) else backend


def keyring_get(username: str) -> str | None:
    backend = _keyring()
    if backend is None:
        return None
    try:
        return backend.get_password(KEYRING_SERVICE, username) or None
    except Exception:  # noqa: BLE001
        # A locked store, a refused access prompt, a missing D-Bus: whatever
        # the backend raises, the answer is "not stored here".
        return None


def keyring_set(username: str, password: str) -> None:
    backend = _keyring()
    if backend is None:
        raise StoreFailed("there is no password store on this machine")
    try:
        backend.set_password(KEYRING_SERVICE, username, password)
    except Exception as exc:  # noqa: BLE001
        # Only the type: a backend's own message is not ours to vouch for,
        # and the password must never ride along in one.
        raise StoreFailed(f"the password store refused ({type(exc).__name__})") from None


def _env_path() -> Path:
    return Path(os.environ["HILAN_ENV"]).expanduser() if os.environ.get("HILAN_ENV") else ENV_FILE


def _env_lines(text: str) -> list[str]:
    """Lines as a .env has them: split on line breaks and nothing else.

    str.splitlines also splits on characters such as U+0085 and U+2028, which a
    password may hold; reading one back cut in two would send half of it.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _env_key(line: str) -> tuple[str, str] | None:
    """KEY and the raw value of a KEY=value line, `export ` allowed in front."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    return key, value.strip()


def read_env() -> dict[str, str]:
    """KEY=value lines from the .env file, if there is one.

    Anything unparseable is skipped rather than fatal: a file people edit by
    hand should not stop the tool because of one stray line.
    """
    path = _env_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    except UnicodeDecodeError:
        raise CredentialsUnavailable(f"{path} is not UTF-8 text — save it as UTF-8") from None
    found = {}
    for line in _env_lines(text):
        parsed = _env_key(line)
        if parsed:
            found[parsed[0]] = _env_unquote(parsed[1])
    return found


def _env_unquote(value: str) -> str:
    """The value a .env line means, as dotenv files are read everywhere.

    'single quotes' are literal; "double quotes" undo the \\ \" \n \r escapes;
    either ends at its closing quote, so a comment after it is not part of the
    value. A bare value ends where a ` #` comment begins.
    """
    if value[:1] == "'" and "'" in value[1:]:
        return value[1:value.index("'", 1)]
    if value[:1] == '"':
        end, i = None, 1
        while i < len(value):
            if value[i] == "\\":
                i += 2
                continue
            if value[i] == '"':
                end = i
                break
            i += 1
        if end is None:
            return value
        inner, out, i = value[1:end], [], 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner) and inner[i + 1] in '\\"nr':
                out.append({"n": "\n", "r": "\r"}.get(inner[i + 1], inner[i + 1]))
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    hash_at = next((i for i in range(1, len(value)) if value[i] == "#" and value[i - 1] in " \t"), None)
    return value[:hash_at].rstrip() if hash_at is not None else value


def _env_quote(value: str) -> str:
    """A value that reads back exactly as written.

    A password can begin or end with a space or a quote, or hold a # or a
    backslash; written bare, any of those would come back changed, and a cron
    job would then keep sending a wrong password until the account locks.
    """
    plain = value == value.strip() and not any(c in value for c in "\"'#\\\n\r")
    if plain and value:
        return value
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\r", "\\r"))
    return '"' + escaped + '"'


def write_env(values: dict[str, str]) -> None:
    """Set these keys in the .env, leaving anything else in it alone."""
    path = _env_path()
    lines, seen = [], set()
    try:
        lines = _env_lines(path.read_text(encoding="utf-8-sig"))
    except OSError:
        pass
    while lines and not lines[-1]:
        lines.pop()
    out = []
    for line in lines:
        parsed = _env_key(line)
        key = parsed[0] if parsed else None
        if key in values:
            if values[key] is None:              # asked to drop it
                continue
            if key not in seen:
                out.append(f"{key}={_env_quote(values[key])}")
                seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen and value is not None:
            out.append(f"{key}={_env_quote(value)}")
    if path.parent == CONFIG_DIR:
        ensure_private_dir(path.parent)
    else:
        # HILAN_ENV may point into a home or project folder: the file is made
        # private, the folder is not ours to lock down.
        path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, "\n".join(out) + "\n")


def _file_get(username: str) -> str | None:
    values = read_env()
    if values.get("HILAN_USER") not in (None, username):
        return None
    return values.get("HILAN_PASSWORD")


def _file_set(username: str, password: str) -> None:
    write_env({"HILAN_USER": username, "HILAN_PASSWORD": password})


def password_get(username: str) -> str | None:
    """The stored password: the password store where there is one, else the file.

    The file is consulted even where a store exists, since a store that refused
    a write once will have left the password there.
    """
    return keyring_get(username) or _file_get(username)


def password_set(username: str, password: str) -> str:
    """Store it, and say where it went — and leave no older copy elsewhere.

    Falls back to the file if the password store will not take it: a login
    that worked should not be thrown away because the store refused. Whichever
    takes it, the other must not keep an old one: the store is read first, so
    an old password left there would be sent instead of the new one, and a
    plain-text copy left in the file is a copy nobody needs.
    """
    try:
        keyring_set(username, password)
    except StoreFailed:
        _keyring_forget(username)
        _file_set(username, password)
        return str(_env_path())
    _file_forget()
    return store_name()


def _keyring_forget(username: str) -> None:
    backend = _keyring()
    if backend is None:
        return
    try:
        backend.delete_password(KEYRING_SERVICE, username)
    except Exception:  # noqa: BLE001 — nothing stored, or a store that will not say
        pass


def _file_forget() -> None:
    """Drop the credentials this tool wrote to the .env, keeping anything else."""
    try:
        values = read_env()
    except CredentialsUnavailable:
        return
    if "HILAN_PASSWORD" in values or "HILAN_USER" in values:
        write_env({"HILAN_USER": None, "HILAN_PASSWORD": None})


def store_name() -> str:
    """What to call the store, so the user is told where their password goes.

    Named after the backend keyring actually chose, not after the platform: a
    Mac can be configured to use something other than the Keychain.
    """
    backend = _keyring()
    if backend is None:
        return str(_env_path())
    module = type(backend).__module__
    if module.endswith(".macOS"):
        return "the macOS Keychain"
    if module.endswith(".Windows"):
        return "Windows Credential Manager"
    return f"the system keyring ({backend.name})"


# -- a password Hilan has refused ----------------------------------------------
#
# Retrying a password Hilan has turned down is how an account gets locked: a
# scheduled run would send it again every time. Once refused, no automatic
# login is tried until `hilan login` is run by hand.

def _refused_file() -> Path:
    return CONFIG_DIR / "login-refused"


def refused_since() -> str | None:
    """When Hilan last refused the stored password, if it has and nothing changed since."""
    try:
        return _refused_file().read_text(encoding="utf-8").strip() or "earlier"
    except OSError:
        return None


def mark_refused(reason: str = "") -> None:
    """Stop automatic logins until `hilan login` succeeds.

    A directory that cannot be written is warned about, not raised: the error
    the person needs to see is Hilan's answer, not the file's.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        ensure_private_dir(CONFIG_DIR)
        write_private(_refused_file(), f"{stamp}, {reason}" if reason else stamp)
    except OSError as exc:
        logging.getLogger("hilan").warning(
            "could not record that Hilan refused the login (%s); the next run will try again",
            type(exc).__name__,
        )


def clear_refused() -> None:
    try:
        _refused_file().unlink()
    except FileNotFoundError:
        pass


class StoreFailed(RuntimeError):
    """The password store refused.

    Carries only what kind of failure it was, never the backend's own message:
    nothing that reaches the screen or the log may risk spelling the password.
    """


class CredentialsUnavailable(RuntimeError):
    """Nothing stored and nobody at the keyboard to ask."""


def resolve_credentials(prompt: bool = True) -> Credentials:
    """Environment → .env file → stored → interactive prompt.

    An explicit export comes first: someone who sets a variable for one command
    means it to win over a file they wrote months ago. Prompting only makes
    sense with a terminal attached; from cron or a pipe a silent password
    prompt would just hang, so say plainly what to run instead.
    """
    interactive = prompt and sys.stdin.isatty()
    from_file = read_env()

    username = (
        os.environ.get("HILAN_USER", "").strip()
        or from_file.get("HILAN_USER", "").strip()
        or load_username()
    )
    if not username:
        if not interactive:
            raise CredentialsUnavailable(
                "no employee number configured — run `hilan login`,"
                " or put HILAN_USER in the .env"
            )
        username = input("Employee number: ").strip()

    password = os.environ.get("HILAN_PASSWORD") or password_get(username)
    if not password:
        if not interactive:
            raise CredentialsUnavailable(
                "no saved password and no terminal to ask on — run"
                f" `hilan login`, or put HILAN_PASSWORD in {_env_path()}"
            )
        import getpass
        password = getpass.getpass(f"Hilan password for {username}: ")
    return Credentials(username=username, password=password)
