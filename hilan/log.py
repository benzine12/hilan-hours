# -*- coding: utf-8 -*-
"""A log you can investigate a failure from, and would not mind someone reading.

Every run leaves a trace: the command, every request with its status, timing and
size, what was parsed and computed, and the whole traceback when something
breaks.

What must never reach it matters as much as what must. A log is the thing people
paste into a chat when they ask for help, so the password, the session cookies
and Hilan's __VIEWSTATE are stripped on the way in rather than trusted not to
turn up. Field names are kept — ``password=<redacted>`` says more than a missing
line, and says nothing secret.

Logging is never allowed to break a run: a directory that cannot be written just
means no log.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path

from .config import CONFIG_DIR, ensure_private_dir

LOG_FILE = CONFIG_DIR / "hilan.log"
LOGGER_NAME = "hilan"

DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_KEEP = 3

#: Form fields whose value is never worth the risk of writing down.
SECRET_FIELDS = ("password", "newPassword", "__VIEWSTATE", "__VIEWSTATEGENERATOR",
                 "__EVENTVALIDATION", "H-XSRF-Token", "verificationCode")
SECRET_HEADERS = ("cookie", "set-cookie", "authorization")

_FIELD = re.compile(
    r"(" + "|".join(re.escape(f) for f in SECRET_FIELDS) + r")=[^&\s]*",
    re.IGNORECASE,
)


class _RedactingFormatter(logging.Formatter):
    """Redacts the whole line, traceback included.

    An exception's own text can carry a request — httpx puts the URL with its
    query string in its errors — so the message is not the only way in.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def _safe_url(url: str) -> str:
    """Scheme, host and path, and the query by its names only.

    A blocklist of field names misses a secret under any other name or
    encoding; names alone say what was asked without what was in it.
    """
    from urllib.parse import parse_qsl, urlsplit

    try:
        parts = urlsplit(str(url))
        names = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
        host = parts.hostname or ""
        return f"{parts.scheme}://{host}{parts.path}" + (f"?{'&'.join(f'{n}=…' for n in names)}" if names else "")
    except ValueError:
        return "<unreadable url>"


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Creates the log, and each fresh file after a rollover, readable by its owner only."""

    def _open(self):
        # O_BINARY on Windows, or the C runtime adds a second \r to every line.
        # O_NOFOLLOW: a log that is a link to some other file is not written through.
        flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND
                 | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(self.baseFilename, flags, 0o600)
        return open(descriptor, self.mode, encoding=self.encoding, errors=self.errors)


def logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def reset() -> None:
    """Drop every handler — used between tests, and harmless otherwise."""
    log = logger()
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()


def redact(text: str) -> str:
    """Blank out secret field values, keeping the field names."""
    return _FIELD.sub(lambda m: f"{m.group(1)}=<redacted>", text or "")


def safe(headers) -> dict:
    """Headers with the ones that carry credentials blanked."""
    return {
        k: ("<redacted>" if k.lower() in SECRET_HEADERS else v)
        for k, v in dict(headers).items()
    }


def setup(*, argv=None, verbose: bool = False,
          max_bytes: int = DEFAULT_MAX_BYTES, keep: int = DEFAULT_KEEP) -> None:
    """Start logging this run. Never raises: no log is better than no run."""
    log = logger()
    log.setLevel(logging.DEBUG)
    reset()

    formatter = _RedactingFormatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    try:
        ensure_private_dir(LOG_FILE.parent)
        handler = _PrivateRotatingFileHandler(
            LOG_FILE, maxBytes=max_bytes, backupCount=keep, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        log.addHandler(handler)
        # Logs that already existed, rotated ones included, may be wider.
        for existing in [Path(LOG_FILE), *Path(LOG_FILE).parent.glob(Path(LOG_FILE).name + ".*")]:
            if existing.is_symlink():
                continue
            try:
                existing.chmod(0o600)
            except OSError:
                pass
    except OSError:
        pass

    if verbose:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        log.addHandler(stream)

    if not log.handlers:
        log.addHandler(logging.NullHandler())

    if argv is not None:
        log.info("run: %s", " ".join(argv))


def request_done(method: str, url: str, status: int, seconds: float,
                 size: int | None = None, body: str | None = None) -> None:
    """One HTTP exchange, with the body redacted when there is one.

    Hilan answers without a content-length, so the size is often unknown —
    omitted rather than reported as zero, which would read as an empty reply.
    """
    line = f"{method} {_safe_url(url)} -> {status} in {seconds:.2f}s"
    if size:
        line += f", {size} bytes"
    if body:
        line += f"  body: {redact(body)}"
    logger().info(line)


def failure(what: str) -> None:
    """The current exception, with its traceback, under a line of context."""
    logger().exception("failed: %s", what)
