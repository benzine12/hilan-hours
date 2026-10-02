# -*- coding: utf-8 -*-
"""Turns an attendance page into structured days.

Hilan stores every displayed value in an ``ov`` ("original value") attribute and
identifies days and their segments through element ids — ``_row_N`` for the day,
``_row_N_K`` for the K-th segment — rather than through nesting. Parsing keys off
those ids, which makes it robust against the page's deeply nested table layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from . import clock as hilan_clock
from .serial import serial_to_date

# Labels Hilan puts in a day's special cell. The same cell doubles as a slot for
# validation messages, so anything not on this list is treated as an error.
HOLIDAY_LABELS = {"חג", "חול המועד"}
EREV_PREFIX = "ערב"
#: Every label the special cell is known to carry, longest first, so that
#: "ערב חג" is not taken for "ערב" and a message after it.
KNOWN_LABELS = ("ערב ראש השנה", 'ערב יוה"כ', "חול המועד", "ערב חג", "חג")

_GRID_RE = re.compile(r"RG_Days_(\d+)_(\d{4})_(\d{2})_reportsGrid_innerBody")
_ROW_RE = re.compile(r"_row_(\d+)$")
_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})")
_SYNC_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

REPORT_FIELDS = ("ManualEntry", "ManualExit", "ManualTotal", "StandardWorkHours", "Comment")
MAX_SEGMENTS = 12


@dataclass(frozen=True)
class ClockSegment:
    entry: time | None
    exit: time | None


@dataclass(frozen=True)
class ReportSegment:
    entry: time | None
    exit: time | None
    total: timedelta | None
    standard: Decimal | None
    comment: str
    symbol: str


@dataclass
class DayRecord:
    date: date
    special: str | None = None
    error: str | None = None
    clock: list[ClockSegment] = field(default_factory=list)
    report: list[ReportSegment] = field(default_factory=list)
    is_error_day: bool = False
    is_absence_day: bool = False
    calendar_label: str = ""
    #: Requirements Hilan states on rows that report nothing yet — a future
    #: day, a day not filled in. The row is not a segment, but its ש. תקן counts.
    idle_standards: list[Decimal] = field(default_factory=list)


@dataclass(frozen=True)
class HilanTotals:
    standard: Decimal | None
    standard_to_date: Decimal | None
    actual: Decimal | None
    productive: Decimal | None
    vacation_balance: Decimal | None


@dataclass
class MonthReport:
    year: int
    month: int
    employee_name: str
    employee_number: str
    days: list[DayRecord]
    totals: HilanTotals | None
    totals_match_month: bool
    clock_synced_at: datetime | None


def _blank(value: str | None) -> bool:
    """Hilan writes an empty cell as the literal text ``&nbsp;``."""
    return value is None or value.strip().replace("\xa0", "") in ("", "&nbsp;")


def _text(value: str | None) -> str:
    return "" if _blank(value) else value.strip()


def _parse_time(value: str | None) -> time | None:
    if _blank(value):
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    return time(hour % 24, minute) if hour <= 24 and minute < 60 else None


def _parse_duration(value: str | None) -> timedelta | None:
    if _blank(value):
        return None
    m = _TIME_RE.match(value.strip())
    return timedelta(hours=int(m.group(1)), minutes=int(m.group(2))) if m else None


def _parse_decimal(value: str | None) -> Decimal | None:
    if _blank(value):
        return None
    return _number(value.strip())


def _number(text: str) -> Decimal | None:
    """A decimal, with the minus sign on either side of it.

    Right-to-left pages often write a negative number as 12.50-. NaN and
    Infinity parse too, and would only crash the arithmetic later.
    """
    if text.endswith("-") and not text.startswith("-"):
        text = "-" + text[:-1]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _classify_special(raw: str) -> tuple[str | None, str | None]:
    """Split the special cell into a holiday label and a validation message.

    On the real page this cell wraps its text in a nested table, so the raw
    text arrives padded with newlines and tabs.

    It can also hold both — a holiday label and a validation message side by
    side ("חול המועד קיים דיווח ללא פרויקט באותה השורה"). Read as one, the
    label would be lost: a holiday would ask a full day, and the message would
    show as Hebrew no one translated.
    """
    text = " ".join(raw.split())
    if not text:
        return None, None
    for label in KNOWN_LABELS:
        if text == label:
            return label, None
        if text.startswith(label + " "):
            return label, text[len(label) + 1:]
        if text.endswith(" " + label):
            return label, text[:-len(label) - 1]
    if text.startswith(EREV_PREFIX):          # an eve this list has not met
        return text, None
    return None, text


def parse_month(html: str) -> MonthReport:
    if not html or not html.strip():
        raise ValueError("empty document")
    soup = BeautifulSoup(html, "html.parser")

    grid = _pick_grid(soup)
    m = _GRID_RE.search(grid.get("id", ""))
    if not m:
        raise ValueError(f"unexpected grid id {grid.get('id')!r}")
    _, year, month = m.group(1), int(m.group(2)), int(m.group(3))
    gid = grid["id"].replace("_reportsGrid_innerBody", "")

    ov_by_id = {
        td["id"]: td.get("ov", "")
        for td in soup.select("td[id]")
        if td.has_attr("ov")
    }

    def ov(cell_id: str) -> str | None:
        return ov_by_id.get(cell_id)

    calendar = _parse_calendar(soup)
    clock_synced_at = _parse_sync(soup)

    days: list[DayRecord] = []
    seen_rows: set[str] = set()
    for tr in soup.select("tr[id]"):
        rm = _ROW_RE.search(tr["id"])
        if not rm:
            continue
        n = rm.group(1)
        # Row numbers repeat in every grid on the page; another grid's row N is
        # this grid's row N again, not a second day.
        if n in seen_rows:
            continue
        seen_rows.add(n)
        label = ov(f"{gid}_cellOf_ReportDate_row_{n}")
        dm = _DATE_RE.search(label or "")
        # A row labelled with another month does not belong to this grid.
        if not dm or int(dm.group(2)) != month:
            continue
        day_date = date(year, month, int(dm.group(1)))

        # Scoped to this grid: a page can carry another month's rows too.
        special_cell = soup.select_one(f'td[id="{gid}_special_row_{n}"]')
        special, error = _classify_special(special_cell.get_text() if special_cell else "")

        clock = []
        for k in range(MAX_SEGMENTS):
            entry = ov(f"{gid}_cellOf_OriginalEntry_ClockReports_row_{n}_{k}")
            if entry is None:
                break
            exit_ = ov(f"{gid}_cellOf_OriginalExit_ClockReports_row_{n}_{k}")
            if _blank(entry) and _blank(exit_):
                continue
            clock.append(ClockSegment(_parse_time(entry), _parse_time(exit_)))

        report, idle_standards = [], []
        for k in range(MAX_SEGMENTS):
            entry = ov(f"{gid}_cellOf_ManualEntry_EmployeeReports_row_{n}_{k}")
            if entry is None:
                break
            values = {
                f: ov(f"{gid}_cellOf_{f}_EmployeeReports_row_{n}_{k}") for f in REPORT_FIELDS
            }
            symbol_cell = soup.select_one(
                f'td[id="{gid}_cellOf_Symbol.SymbolId_EmployeeReports_row_{n}_{k}"] option[selected]'
            )
            symbol = symbol_cell.get_text().strip() if symbol_cell else ""
            # A placeholder row: no times, no total, no symbol. It reports
            # nothing, but the requirement it states is still Hilan's word.
            if (
                _blank(values["ManualEntry"])
                and _blank(values["ManualExit"])
                and _blank(values["ManualTotal"])
                and not symbol
            ):
                stated = _parse_decimal(values["StandardWorkHours"])
                if stated is not None:
                    idle_standards.append(stated)
                continue
            report.append(
                ReportSegment(
                    entry=_parse_time(values["ManualEntry"]),
                    exit=_parse_time(values["ManualExit"]),
                    total=_parse_duration(values["ManualTotal"]),
                    standard=_parse_decimal(values["StandardWorkHours"]),
                    comment=_text(values["Comment"]),
                    symbol=symbol,
                )
            )

        cal = calendar.get(day_date, ("", ""))
        days.append(
            DayRecord(
                date=day_date, special=special, error=error,
                clock=clock, report=report,
                is_error_day="cED" in cal[0].split(),
                is_absence_day="calendarAbcenseDay" in cal[0].split(),
                calendar_label=cal[1],
                idle_standards=idle_standards,
            )
        )

    days.sort(key=lambda d: d.date)
    totals = _parse_legend(soup)
    # The legend panel always shows the current payroll month, never the month
    # being browsed — so it is only trustworthy when the two coincide.
    reference = clock_synced_at.date() if clock_synced_at else hilan_clock.today()
    name, number = _parse_employee(soup)
    return MonthReport(
        year=year, month=month,
        employee_name=name, employee_number=number,
        days=days, totals=totals,
        totals_match_month=(reference.year == year and reference.month == month),
        clock_synced_at=clock_synced_at,
    )


def _pick_grid(soup: BeautifulSoup):
    """Choose the grid for the month the page says it is showing.

    A page can carry more than one month's grid at once. Falling back to
    "whichever comes first" would silently report the wrong month, so the
    hidden currentMonth field decides when there is a choice.
    """
    grids = soup.select('table[id*="_reportsGrid_innerBody"]')
    if not grids:
        raise ValueError("attendance grid not found — is this an attendance page?")
    if len(grids) == 1:
        return grids[0]
    current = soup.select_one('input[name="ctl00$mp$currentMonth"]')
    value = (current.get("value") or "") if current else ""
    cm = re.search(r"(\d{2})/(\d{4})$", value)
    if cm:
        wanted = f"_{cm.group(2)}_{cm.group(1)}_reportsGrid_innerBody"
        for grid in grids:
            if grid.get("id", "").endswith(wanted):
                return grid
    raise ValueError(
        f"page carries {len(grids)} month grids and currentMonth={value!r} "
        "does not identify one of them"
    )


def _parse_calendar(soup: BeautifulSoup) -> dict[date, tuple[str, str]]:
    out: dict[date, tuple[str, str]] = {}
    for td in soup.select("td[days]"):
        try:
            serial = int(td["days"])
        except (TypeError, ValueError):
            continue
        out[serial_to_date(serial)] = (
            " ".join(td.get("class", [])),
            (td.get("title") or "").strip(),
        )
    return out


def _parse_sync(soup: BeautifulSoup) -> datetime | None:
    node = soup.select_one('[id$="LastUpdateLegendText"]')
    if node is None:
        return None
    m = _SYNC_RE.search(node.get_text())
    if not m:
        return None
    d, mo, y, hh, mm = (int(g) for g in m.groups())
    return datetime(y, mo, d, hh, mm)


def _parse_employee(soup: BeautifulSoup) -> tuple[str, str]:
    node = soup.select_one('[id$="lblSelectedName"]')
    if node is None:
        return "", ""
    parts = [p.strip() for p in node.get_text().split("|")]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return parts[0] if parts else "", ""


_LEGEND_PATTERNS = {
    "standard": r"תקן:\s*(-?\d+(?:\.\d+)?-?)",
    "standard_to_date": r"תקן עד היום:\s*(-?\d+(?:\.\d+)?-?)",
    "actual": r"שעות בפועל:\s*(-?\d+(?:\.\d+)?-?)",
    "productive": r"יצרניות:\s*(-?\d+(?:\.\d+)?-?)",
    "vacation_balance": r"הינה:\s*(-?\d+(?:\.\d+)?-?)",
}


def _parse_legend(soup: BeautifulSoup) -> HilanTotals | None:
    node = soup.select_one('[id$="DynamicLegendDiv"]')
    if node is None:
        return None
    text = node.get_text(" ")
    values = {}
    for key, pattern in _LEGEND_PATTERNS.items():
        m = re.search(pattern, text)
        values[key] = _number(m.group(1)) if m else None
    return HilanTotals(**values)
