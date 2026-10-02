# -*- coding: utf-8 -*-
"""Presentation: one screen that answers "am I up or down, right now?".

Hilan's own Hebrew goes through ``text.display``, which translates the terms it
knows and reorders the rest so it reads correctly on a terminal without bidi.
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timedelta

from rich.console import Console
from rich import box
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from .calc import (Analysis, DayCalc, IssueKind, _clock_exit, _open_entry, _segment_duration,
                   _wall, hrs, is_absence)
from .text import display

MONTHS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

PENDING_KINDS = {IssueKind.PENDING_SYNC}

ELLIPSIS = "\u2026"
EN_DASH = "\u2013"


def _signed(td: timedelta) -> Text:
    text = hrs(td)
    if td > timedelta(0):
        return Text(f"+{text}", style="bold green")
    if td < timedelta(0):
        return Text(text, style="bold red")
    return Text(hrs(timedelta(0)), style="bold")


def _d(day) -> str:
    return day.strftime("%d/%m")


def _print(console: Console, lines: list[Text]) -> None:
    for line in lines:
        console.print(line)


INDENT = "           "


def _pack(parts: list[str], width: int) -> list[str]:
    """Group the parts into lines, never splitting one.

    Letting the terminal wrap instead breaks a sentence mid-phrase, and a
    wrapped number reads as two numbers.
    """
    lines, current = [], ""
    for part in parts:
        candidate = f"{current} · {part}" if current else part
        if current and len(INDENT) + len(candidate) > width:
            lines.append(current)
            current = part
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _row(label: str, value: Text, parts: list[str], console: Console) -> list[Text]:
    """A padded label, the number in its own column, then the why.

    The why joins the headline when it fits and moves below when it does not.
    """
    line = Text(f"  {label:<8}", style="bold")
    line.append(" " * max(0, 6 - len(value.plain)))
    line.append_text(value.copy())
    detail = " · ".join(parts)
    if len(line.plain) + 3 + len(detail) <= console.width:
        line.append(f"   {detail}", style="dim")
        return [line]
    return [line] + [
        Text(INDENT + chunk, style="dim") for chunk in _pack(parts, console.width)
    ]


def render_summary(analysis: Analysis, console: Console) -> None:
    r = analysis.report
    console.print()
    # No name here. It identifies nobody in a single-user tool, and as the only
    # Hebrew on screen it would have to be pre-reversed for a terminal without
    # bidi — which then reads backwards anywhere that does its own, making a
    # working tool look broken. The number is still in --json.
    title = Text("  Hilan", style="bold cyan") + Text(f" · {MONTHS[r.month]} {r.year}")
    console.print(title)
    # At most 74 wide and never wider than the terminal, so it does not wrap on
    # a narrow screen, nor on the 80-column default once the indent is counted.
    console.print(Text("  " + "─" * max(20, min(74, console.width - 4)), style="dim"))
    console.print()

    now, month = analysis.now, analysis.month

    # Two true numbers, and the live one leads: "how am I doing right now" is
    # the question, and the banked figure stops at last night.
    live = analysis.live_balance
    if live != now.balance:
        _print(console, _row("NOW", _signed(live), ["stopping now"], console))
    _print(console, _row(
        "BANKED", _signed(now.balance),
        [f"worked {hrs(now.worked)}"
         + (f" + credited {hrs(now.credited)}" if now.credited else ""),
         f"required {hrs(now.standard)}",
         f"to {_d(analysis.settled_through)}"],
        console,
    ))

    remaining = month.remaining
    if remaining > timedelta(0):
        value, tail = Text(hrs(remaining), style="bold yellow"), f"left of {hrs(month.standard)}"
    else:
        value, tail = Text(hrs(-remaining), style="bold green"), f"over {hrs(month.standard)}"
    _print(console, _row("MONTH", value, [tail], console))

    _render_today(analysis, console)
    _render_reconciliation(analysis, console)
    _render_freshness(analysis, console)
    _render_issues(analysis, console)
    console.print()


def _render_reconciliation(analysis: Analysis, console: Console) -> None:
    rec = analysis.reconciliation
    if rec is None:
        if not analysis.report.totals_match_month:
            console.print()
            console.print(
                "  Hilan's summary covers the current month, not this one"
                " — comparison skipped.",
                style="dim",
            )
        return
    totals = analysis.report.totals
    console.print()
    bits = [f"actual {rec.reported}"]
    if totals and totals.productive is not None:
        bits.append(f"productive {totals.productive}")
    # Hilan's figure less ours: minus when Hilan counts fewer hours than the
    # days add up to, plus when it counts more.
    bits.append(f"gap -{hrs(rec.gap)}" if rec.gap > timedelta(0) else f"gap +{hrs(-rec.gap)}")
    head = Text("  Hilan reports:  ", style="bold")
    joined = " · ".join(bits)
    if len(head.plain) + len(joined) <= console.width:
        head.append(joined, style="dim")
        console.print(head)
    else:
        console.print(Text("  Hilan reports:", style="bold"))
        for chunk in _pack(bits, console.width):
            console.print(Text(INDENT + chunk, style="dim"))
    for day in rec.candidates:
        detail = next(
            (i.detail for i in analysis.issues if i.date == day.date and i.actionable),
            "",
        )
        console.print(
            f"    └─ {_d(day.date)} · {hrs(day.worked)}"
            + (f" · {display(detail)}" if detail else ""),
            style="yellow",
        )
    if not rec.candidates:
        for n, chunk in enumerate(
            textwrap.wrap("no single day accounts for the whole gap",
                          max(20, console.width - 7))
        ):
            console.print(("    └─ " if n == 0 else " " * 7) + chunk, style="dim")


def _render_freshness(analysis: Analysis, console: Console) -> None:
    synced = analysis.report.clock_synced_at
    console.print()
    if synced:
        console.print(
            f"  Clock synced through {synced.strftime('%d/%m %H:%M')}", style="dim"
        )


def render_brief(analysis: Analysis, console: Console) -> None:
    """The default screen: hours actually done, and hours in the bank.

    Two numbers over two periods, so both say which period they mean. Worked
    covers the whole month including today, because those hours were done.
    Banked stops at the last settled day, because an unfinished day cannot be
    weighed against a full day's requirement without inventing a deficit.
    """
    r = analysis.report
    console.print()
    console.print(Text("Hilan", style="bold cyan") + Text(f" · {MONTHS[r.month]} {r.year}"))
    console.print()

    worked = Text("  WORKED  ", style="bold")
    worked.append(f"{hrs(analysis.month.worked):>7}", style="bold")
    worked.append("   recorded this month", style="dim")
    f = analysis.forecast
    if f is not None and f.entry is not None:
        # Time on the clock right now that Hilan has not written down yet.
        running = f.worked_so_far - f.earlier_today
        if running > timedelta(0):
            worked.append(f" · {hrs(running)} running", style="yellow")
    console.print(worked)

    banked = Text("  BANKED  ", style="bold")
    signed = _signed(analysis.live_balance).copy()
    banked.append(" " * max(0, 7 - len(signed.plain)))
    banked.append_text(signed)
    if analysis.live_balance != analysis.now.balance:
        banked.append(
            f"   right now · {hrs(analysis.now.balance)} settled"
            f" to {_d(analysis.settled_through)}", style="dim")
    else:
        banked.append(f"   through {_d(analysis.settled_through)}", style="dim")
    console.print(banked)

    actionable = [i for i in analysis.issues if i.actionable]
    if actionable:
        # Never drop a day that is costing hours; just do not spell it out here.
        n = len(actionable)
        console.print()
        console.print(
            f"  {n} day{'s' if n > 1 else ''} need{'' if n > 1 else 's'} attention"
            " — run: hilan",
            style="dim",
        )
    console.print()


def _punches(record, now) -> list[tuple[str, str, bool]]:
    """Today's sessions: the times, what each was worth, and whether it is open.

    A stacked punch without its own hours makes the reader subtract times in
    their head, which is the arithmetic this tool exists to do for them.
    """
    out = []
    open_entry = _open_entry(record)
    for seg in record.report:
        if not (seg.entry or seg.exit or seg.total is not None):
            continue
        # Built outside the f-string: Python before 3.12 allows no backslash
        # inside an f-string's braces.
        # An exit that has reached only the clock column closes the row, as in
        # the sums: shown as it will be, not as running on.
        synced = (_clock_exit(record, seg.entry)
                  if seg.entry and not seg.exit and seg.total is None else None)
        last = seg.exit or synced
        start = seg.entry.strftime("%H:%M") if seg.entry else ELLIPSIS
        end = last.strftime("%H:%M") if last else ELLIPSIS
        # Hours reported without times show as hours, not as an open session.
        times = f"{start}{EN_DASH}{end}" if (seg.entry or seg.exit) else "hours"
        running = bool(seg.entry and seg.entry == open_entry)
        if running:
            started = datetime.combine(record.date, seg.entry)
            worth = max(timedelta(0), now - started)
        else:
            # A closed row without a total is worth its times, as in the sums.
            worth = _segment_duration(seg.entry, last, seg.total) or timedelta(0)
        out.append((times, hrs(worth), running))
    # A clock-in the report has not caught up with is today's session too.
    if open_entry is not None and not any(seg.entry == open_entry for seg in record.report):
        started = datetime.combine(record.date, open_entry)
        out.append((f"{open_entry:%H:%M}{EN_DASH}{ELLIPSIS}",
                    hrs(max(timedelta(0), now - started)), True))
    return out


def _render_today(analysis: Analysis, console: Console) -> None:
    """Today's punches, what they add up to, and when you could walk out.

    None of it reaches BANKED — today is never settled — and the NOW line says
    it is conditional, so nothing here needs to repeat it. The punches
    carry their own explanation: a leave time that is not
    simply the entry plus the requirement is accounted for by the earlier
    session sitting right beside it.
    """
    f = analysis.forecast
    if f is None:
        return
    record = next((d.record for d in analysis.days if d.date == f.day), None)
    punches = _punches(record, analysis.clock) if record else []

    head = "  TODAY    " + f"{_d(f.day)}   "
    line = Text("  TODAY    ", style="bold")
    line.append(f"{_d(f.day)}   ", style="dim")
    # An open punch renders narrower than a closed one, so pad to the widest
    # and the hours stay in a column.
    width = max((len(t) for t, _, _ in punches), default=0)
    if punches:
        times, worth, running = punches[0]
        line.append(times.ljust(width), style="cyan")
        line.append(f"   {worth}", style="dim")
        # The ellipsis already says it is open; the word is a courtesy a narrow
        # screen cannot afford.
        if running and len(line.plain) + 9 <= console.width:
            line.append("  running", style="dim")
    else:
        line.append("nothing recorded yet", style="dim")
    console.print(line)

    # The rest stack under the first, so a day of several sessions reads down
    # the page rather than off the side of it.
    for times, worth, running in punches[1:]:
        row = Text(" " * len(head)) + Text(times.ljust(width), style="cyan")
        row.append(f"   {worth}", style="dim")
        if running and len(row.plain) + 9 <= console.width:
            row.append("  running", style="dim")
        console.print(row)

    parts = [f"{hrs(f.worked_so_far)} of {hrs(f.required)}"]
    if f.credited:
        parts.append(f"{hrs(f.credited)} leave")
    parts.append("done for today" if f.done_for_today else f"{hrs(f.remaining)} to go")
    # Once the day is made, both leave times are in the past and say nothing.
    if f.leave_to_close_today and not f.done_for_today:
        how = " (assumed)" if f.entry_assumed else ""
        parts.append(f"leave {f.leave_to_close_today.strftime('%H:%M')} to close{how}")
        # Compared as time after the entry: 21:30 comes before 01:00 the next morning.
        if f.leave_to_end_level and f.entry and (
            _wall(f.entry, f.leave_to_end_level) < _wall(f.entry, f.leave_to_close_today)
        ):
            parts.append(f"{f.leave_to_end_level.strftime('%H:%M')} to end level")
    elif not f.done_for_today and f.entry is None and not punches:
        # Only where Hilan shows nothing for today. With punches on screen it
        # reads as advice to supply what the tool can plainly see.
        parts.append("not clocked in — pass --in HH:MM")
    for chunk in _pack(parts, console.width):
        console.print(Text(INDENT + chunk, style="dim"))


def _render_issues(analysis: Analysis, console: Console) -> None:
    actionable = [i for i in analysis.issues if i.actionable]
    pending = [i for i in analysis.issues if not i.actionable]

    if actionable:
        console.print()
        console.print("  ⚠ Needs attention", style="bold yellow")
        for issue in actionable:
            _issue(console, issue, "yellow")
    if pending:
        console.print()
        console.print("  Waiting to sync (fixes itself)", style="dim")
        for issue in pending:
            _issue(console, issue, "dim")
    if not actionable and not pending:
        console.print()
        console.print("  No problem days.", style="green")


def _issue(console: Console, issue, style: str) -> None:
    """A day and its message, continuing under the message, not at the margin.

    Wrapped here rather than by rich's Padding, which pads every line out to the
    console width and leaves trailing spaces in anything copied out.
    """
    head = f"    {_d(issue.date)}  "
    body = display(issue.detail) or ""
    for n, chunk in enumerate(
        textwrap.wrap(body, max(20, console.width - len(head))) or [""]
    ):
        console.print(Text((head if n == 0 else " " * len(head)) + chunk, style=style))


def render_days(analysis: Analysis, console: Console, *, only_week: bool = False) -> None:
    # The week can begin in the month before; its days come from that month's
    # page, which was fetched for exactly this.
    days = analysis.week_rows if only_week else analysis.days
    table = Table(box=None, pad_edge=False, header_style="bold")
    for col in ("date", "day", "clock", "reported", "total", "required", "Δ", "note"):
        table.add_column(col, justify="left" if col == "note" else "right")

    for d in days:
        rec = d.record
        clock = _segments(rec.clock)
        rep = _segments(rec.report)
        notes = []
        if rec.special:
            notes.append(display(rec.special))
        notes += [
            display(s) for s in sorted(
                {s.symbol for s in rec.report if is_absence(s.symbol)}
            )
        ]
        if rec.error:
            notes.append("⚠")
        settled = d.date <= analysis.settled_through
        delta = (
            _signed(d.balance)
            if settled and (d.worked or d.credited or d.standard)
            else Text("")
        )
        table.add_row(
            _d(d.date), WEEKDAYS[d.date.weekday()], clock, rep,
            hrs(d.worked) if d.worked else "—",
            hrs(d.standard) if d.standard else "—",
            delta, " ".join(n for n in notes if n),
        )
    console.print()
    console.print(table)


def _segments(segments) -> str:
    """Render a day's segments, skipping ones that hold no times at all."""
    parts = [
        f"{s.entry.strftime('%H:%M') if s.entry else '—'}"
        f"–{s.exit.strftime('%H:%M') if s.exit else '—'}"
        for s in segments
        if s.entry or s.exit
    ]
    return " ".join(parts) if parts else "—"


def _str_or_none(value) -> str | None:
    """A figure as text, or null in the JSON when Hilan did not show it."""
    return None if value is None else str(value)


def _forecast_dict(f) -> dict | None:
    """Today, as the TODAY block shows it; null when the page has no row for today."""
    if f is None:
        return None
    at = lambda t: t.strftime("%H:%M") if t else None  # noqa: E731
    return {
        "day": f.day.isoformat(), "required": hrs(f.required),
        "worked_so_far": hrs(f.worked_so_far), "earlier_today": hrs(f.earlier_today),
        "credited": hrs(f.credited), "remaining": hrs(f.remaining), "done": f.done_for_today,
        "entry": at(f.entry), "entry_assumed": f.entry_assumed,
        "leave_to_close": at(f.leave_to_close_today), "leave_to_end_level": at(f.leave_to_end_level),
    }


def to_dict(analysis: Analysis) -> dict:
    def period(p):
        return {
            "worked": hrs(p.worked), "credited": hrs(p.credited),
            "standard": hrs(p.standard), "balance": hrs(p.balance),
        }

    return {
        "year": analysis.report.year,
        "month": analysis.report.month,
        "employee": analysis.report.employee_number,
        "today": analysis.today.isoformat(),
        "settled_through": analysis.settled_through.isoformat(),
        "clock_synced_at": (
            analysis.report.clock_synced_at.isoformat()
            if analysis.report.clock_synced_at else None
        ),
        "now": period(analysis.now),
        # What NOW shows: the bank as it would stand if today stopped this minute.
        "live_balance": hrs(analysis.live_balance),
        "forecast": _forecast_dict(analysis.forecast),
        "week": period(analysis.week) | {
            "start": analysis.week_start.isoformat(),
            "end": analysis.week_end.isoformat(),
            "in_scope": analysis.week_in_scope,
            "days": [d.isoformat() for d in analysis.week_days],
        },
        "month_totals": period(analysis.month) | {"remaining": hrs(analysis.month.remaining)},
        "hilan": (
            {
                "standard": _str_or_none(analysis.report.totals.standard),
                "standard_to_date": _str_or_none(analysis.report.totals.standard_to_date),
                "actual": _str_or_none(analysis.report.totals.actual),
                "productive": _str_or_none(analysis.report.totals.productive),
                "applies_to_this_month": analysis.report.totals_match_month,
            }
            if analysis.report.totals else None
        ),
        "reconciliation": (
            {
                "computed": hrs(analysis.reconciliation.computed),
                "reported": str(analysis.reconciliation.reported),
                "gap": hrs(analysis.reconciliation.gap),
                "candidates": [d.date.isoformat() for d in analysis.reconciliation.candidates],
            }
            if analysis.reconciliation else None
        ),
        "issues": [
            {
                "date": i.date.isoformat(), "kind": i.kind.value,
                "detail": i.detail, "actionable": i.actionable,
            }
            for i in analysis.issues
        ],
        # Hebrew stays as Hilan wrote it here: JSON is read by programs, which do
        # not care about display order, and losing the original would be worse.
        "days": [
            {
                "date": d.date.isoformat(), "worked": hrs(d.worked),
                "credited": hrs(d.credited), "standard": hrs(d.standard),
                "balance": hrs(d.balance), "special": d.record.special,
                "error": d.record.error,
            }
            for d in analysis.days
        ],
    }
