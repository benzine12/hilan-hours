# -*- coding: utf-8 -*-
"""A local record of every settled day, kept across runs.

Hilan shows one month at a time and its summary panel always describes the
current one, so any question spanning months means fetching them again. This
keeps what has already been seen.

Only settled days are written. An unsettled day is still moving, and a history
that rewrites itself is not a history. Hilan can also amend a day long after the
fact — approve a missing exit, restate a requirement — so a day that comes back
different from what was stored is reported rather than quietly overwritten. That
silent kind of change is the whole reason this tool exists.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .calc import Analysis, hrs
from .config import CONFIG_DIR, ensure_private_dir, write_private
from .text import display, english

HISTORY_FILE = CONFIG_DIR / "history.json"

#: Where the last run moved an unreadable history.json, so it can say so.
last_set_aside: Path | None = None

FIELDS = ("worked", "credited", "required", "balance", "punches")


@dataclass(frozen=True)
class Amendment:
    """A day Hilan has restated since it was last recorded."""

    day: date
    was: dict
    now: dict


@dataclass(frozen=True)
class Totals:
    label: str
    days: int
    worked: Decimal
    credited: Decimal
    required: Decimal

    @property
    def balance(self) -> Decimal:
        return self.worked + self.credited - self.required


def _path(path: Path | None) -> Path:
    return path or HISTORY_FILE


def load(*, path: Path | None = None) -> dict[str, dict]:
    """Everything recorded so far, keyed by ISO date.

    A file that cannot be read is treated as empty: losing the history is bad,
    but refusing to report this month's hours because of it would be worse.
    """
    target = _path(path)
    try:
        stored = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _punches(record) -> list[str]:
    """The day's entries and exits as written, an open one marked rather than dropped.

    Totals answer "how much"; only these answer "when", which is usually what is
    being checked months later.
    """
    return [
        f"{seg.entry.strftime('%H:%M') if seg.entry else '?'}"
        f"-{seg.exit.strftime('%H:%M') if seg.exit else '?'}"
        for seg in record.report
        if seg.entry or seg.exit
    ]


def _row(day) -> dict:
    # As Hilan wrote them, or their English: never the display form, which
    # reverses Hebrew for a terminal and would be stored backwards.
    notes = [english(day.record.special) or day.record.special or ""]
    notes += [
        english(seg.symbol) or seg.symbol
        for seg in day.record.report
        if seg.symbol and day.credited
    ]
    return {
        "punches": _punches(day.record),
        "worked": hrs(day.worked),
        "credited": hrs(day.credited),
        "required": hrs(day.standard),
        "balance": hrs(day.balance),
        "note": " ".join(n for n in dict.fromkeys(notes) if n),
    }


def _load_for_update(target: Path) -> dict[str, dict]:
    """What is recorded, before it is rewritten — never by losing it.

    A file that is not valid JSON (a run killed mid-write, a disk that filled up,
    a hand edit gone wrong) is moved aside rather than overwritten, so months
    recorded before are still there to recover. A file that cannot be read at
    all raises: writing over it would be worse.
    """
    global last_set_aside
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        stored = json.loads(text)
        if not isinstance(stored, dict):
            raise ValueError("not an object")
        return stored
    except ValueError:
        aside = target.with_name(f"{target.name}.unreadable-{datetime.now():%Y%m%d-%H%M%S-%f}")
        try:
            target.replace(aside)
        except FileNotFoundError:            # another run moved it aside first
            return {}
        last_set_aside = aside
        logging.getLogger("hilan").warning("history unreadable; moved aside to %s", aside)
        return {}


def record(analysis: Analysis, *, path: Path | None = None) -> list[Amendment]:
    """Store every settled day of this analysis; report any that changed."""
    target = _path(path)
    kept = _load_for_update(target)
    amendments = []

    for day in analysis.days:
        if day.date > analysis.settled_through:
            continue
        key = day.date.isoformat()
        fresh = _row(day)
        previous = kept.get(key)
        if previous and any(previous.get(f) != fresh[f] for f in FIELDS):
            amendments.append(Amendment(day=day.date, was=previous, now=fresh))
        kept[key] = fresh

    # Written whole to a temporary file that then replaces the old one: a run
    # killed halfway leaves the previous history intact, not a torn file.
    ensure_private_dir(target.parent)
    write_private(target, json.dumps(dict(sorted(kept.items())), ensure_ascii=False, indent=2))
    return amendments


def _sum(rows, label: str) -> Totals:
    return Totals(
        label=label,
        days=len(rows),
        worked=sum((Decimal(r["worked"]) for r in rows), Decimal(0)),
        credited=sum((Decimal(r["credited"]) for r in rows), Decimal(0)),
        required=sum((Decimal(r["required"]) for r in rows), Decimal(0)),
    )


def by_month(*, path: Path | None = None) -> list[Totals]:
    months: dict[str, list[dict]] = {}
    for key, row in sorted(load(path=path).items()):
        months.setdefault(key[:7], []).append(row)
    return [_sum(rows, label) for label, rows in months.items()]


def total(*, path: Path | None = None) -> Totals:
    return _sum(list(load(path=path).values()), "total")
