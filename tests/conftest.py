# -*- coding: utf-8 -*-
"""Renders the captured attendance data back into Hilan-shaped HTML.

Hilan writes every value into an ``ov`` ("original value") attribute on the
cell, and identifies days and segments purely through element ids
(``_row_N`` / ``_row_N_K``) rather than nesting — so a flattened table is
indistinguishable from the real page as far as the parser is concerned.
One quirk is reproduced deliberately: an empty cell arrives as the literal
text ``&nbsp;``, because Hilan escapes the entity a second time.
"""

from __future__ import annotations

import html
import sys
from datetime import datetime

import httpx
import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail

from capture import CAPTURES

REPORT_FIELDS = ("ManualEntry", "ManualExit", "ManualTotal", "StandardWorkHours", "Comment")


def _cell(cell_id: str, value: str) -> str:
    return f'<td id="{cell_id}" ov="{html.escape(value, quote=True)}"></td>'


def build_html(capture: dict) -> str:
    gid = capture["gid"]
    rows = []
    for n, dom, weekday, special, clock, report in capture["rows"]:
        # Hilan joins "יום" and the weekday letter with a no-break space.
        label = f"{dom} שבת" if weekday == "שבת" else f"{dom} יום\u00a0{weekday}"
        cells = [_cell(f"{gid}_cellOf_ReportDate_row_{n}", label)]
        if special is not None:
            cells.append(
                f'<td id="{gid}_special_row_{n}" class="HolidayDay">'
                f"{html.escape(special)}</td>"
            )
        for k, (entry, exit_) in enumerate(clock):
            cells.append(_cell(f"{gid}_cellOf_OriginalEntry_ClockReports_row_{n}_{k}", entry))
            cells.append(_cell(f"{gid}_cellOf_OriginalExit_ClockReports_row_{n}_{k}", exit_))
        for k, segment in enumerate(report):
            *values, symbol = segment
            for field, value in zip(REPORT_FIELDS, values):
                cells.append(_cell(f"{gid}_cellOf_{field}_EmployeeReports_row_{n}_{k}", value))
            cells.append(
                f'<td id="{gid}_cellOf_Symbol.SymbolId_EmployeeReports_row_{n}_{k}">'
                f'<select><option selected="selected">{html.escape(symbol)}</option>'
                f"</select></td>"
            )
        rows.append(f'<tr id="{gid}_row_{n}">{"".join(cells)}</tr>')

    calendar = "".join(
        f'<td days="{serial}" class="{cls}" title="{html.escape(title, quote=True)}"></td>'
        for serial, cls, title in capture["calendar"]
    )
    return (
        '<!DOCTYPE html><html lang="he"><head><meta charset="utf-8"></head><body>'
        '<div id="fixture-root">'
        f'<span id="ctl00_mp_Strip_lblSelectedName">{html.escape(capture["who"])}</span>'
        f'<input type="hidden" name="ctl00$mp$currentMonth" value="{capture["currentMonth"]}">'
        f'<span id="ctl00_mp_LastUpdateLegendText">{html.escape(capture["sync"])}</span>'
        f'<div id="ctl00_mp_DynamicLegendDiv">{capture["legend"]}</div>'
        f'<table id="calendar_container"><tbody><tr>{calendar}</tr></tbody></table>'
        f'<table id="{gid}_reportsGrid_innerBody"><tbody>{"".join(rows)}</tbody></table>'
        "</div></body></html>"
    )


@pytest.fixture(scope="session")
def september_html() -> str:
    return build_html(CAPTURES["2026-09"])


@pytest.fixture(scope="session")
def august_html() -> str:
    return build_html(CAPTURES["2026-08"])


#: A Hilan site that belongs to nobody, so no test depends on a real company's.
TEST_URL = "https://example.net.hilan.co.il"


@pytest.fixture(autouse=True)
def hilan_url(monkeypatch):
    """Every test talks to the same made-up site unless it says otherwise."""
    monkeypatch.setenv("HILAN_URL", TEST_URL)
    return TEST_URL


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may reach the real network.

    A test that relies on some other call being made first to keep it offline
    starts hitting the live site the moment that call goes away. Tests use
    httpx.MockTransport, which does not go through this class, so the block
    costs them nothing.
    """

    def refuse(self, request, *args, **kwargs):
        raise RuntimeError(
            f"a test tried to reach {request.url} — use httpx.MockTransport"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


#: POSIX file modes do not exist on Windows; a chmod there only toggles read-only.
posix_modes = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows has no POSIX file modes"
)


class MemoryKeyring(KeyringBackend):
    """A password store that lives and dies with one test."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


#: The moment every test runs at, unless it is marked ``real_clock``: midday on
#: the made-up September's 23rd. Without it, what a test sees depends on the
#: day it happens to run — September's days are "today" once, and past after.
TEST_NOW = datetime(2026, 9, 23, 12, 0)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_clock: run against the real clock, not TEST_NOW")
    # Before anything asks keyring for the machine's backend: asking probes the
    # real password store (D-Bus on Linux), which a test run has no business doing.
    keyring.set_keyring(MemoryKeyring())


@pytest.fixture(autouse=True)
def fixed_clock(request, monkeypatch):
    """Every test runs at TEST_NOW in Israel, unless it asks for the real clock."""
    if request.node.get_closest_marker("real_clock") is None:
        monkeypatch.setattr("hilan.clock.now", lambda: TEST_NOW)


@pytest.fixture(autouse=True)
def memory_keyring():
    """No test may read or write the machine's real password store."""
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def no_keyring():
    """A machine with no password store at all — a server without a desktop."""
    keyring.set_keyring(fail.Keyring())


@pytest.fixture(autouse=True)
def private_config(tmp_path_factory, monkeypatch):
    """Every test gets its own empty config directory and a clean environment.

    Without this a test reads whatever the developer running it has configured
    — their employee number, their stored session — and appends to their log.
    """
    from hilan import client, config, history, log

    home = tmp_path_factory.mktemp("config")
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(config, "COOKIE_FILE", home / "cookies.json")
    monkeypatch.setattr(config, "ENV_FILE", home / ".env")
    monkeypatch.setattr(client, "COOKIE_FILE", home / "cookies.json")
    monkeypatch.setattr(log, "LOG_FILE", home / "hilan.log")
    monkeypatch.setattr(history, "HISTORY_FILE", home / "history.json")
    monkeypatch.setattr(history, "last_set_aside", None)
    for name in ("HILAN_USER", "HILAN_PASSWORD", "HILAN_ENV", "HILAN_HOME", "HILAN_BIDI"):
        monkeypatch.delenv(name, raising=False)
    yield home
    # Windows will not delete a directory while the log file in it is open.
    log.reset()
