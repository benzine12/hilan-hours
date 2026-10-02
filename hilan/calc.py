# -*- coding: utf-8 -*-
"""Turns parsed days into the numbers the tool exists to show.

The guiding idea is that both sides of a balance must be cut at the same date.
Hilan's punches land a couple of hours after you actually leave, so counting
today's full requirement against hours that have not synced yet would invent a
deficit that does not exist. Everything is therefore measured up to
``settled_through`` — the last day whose data can be trusted to be complete.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Sequence

from . import clock
from .parser import DayRecord, MonthReport
from .text import SYMBOLS, english
from .standard import daily_standard, hours

# Symbols that mean "this person was working".
WORK_SYMBOLS = {"נוכחות", "נכח", "עבודה מהבית", "עבודה במילואים", "מפגש עובד מנהל"}

DEFAULT_MISMATCH = timedelta(minutes=15)


def is_absence(symbol: str) -> bool:
    """Anything labelled but not a work symbol is an absence.

    Treating unknown labels as absences is the safer default: Hilan's list of
    leave types is long and grows, while the set of work symbols is small.
    """
    return bool(symbol) and symbol not in WORK_SYMBOLS


class IssueKind(Enum):
    MISSING_CLOCK_OUT = "missing_clock_out"
    OPEN_DAY = "open_day"
    MISSING_DAY = "missing_day"
    CLOCK_MISMATCH = "clock_mismatch"
    HILAN_ERROR = "hilan_error"
    PENDING_SYNC = "pending_sync"
    STANDARD_MISMATCH = "standard_mismatch"
    UNKNOWN_SYMBOL = "unknown_symbol"


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    date: date
    detail: str
    actionable: bool


@dataclass
class DayCalc:
    record: DayRecord
    worked: timedelta
    credited: timedelta
    standard: timedelta
    #: What the rule alone would have required, when Hilan stated otherwise.
    rule_standard: timedelta | None = None
    #: Hilan's rows stated requirements that contradict each other.
    stated_conflict: bool = False

    @property
    def date(self) -> date:
        return self.record.date

    @property
    def balance(self) -> timedelta:
        return self.worked + self.credited - self.standard


@dataclass(frozen=True)
class PeriodTotals:
    worked: timedelta
    credited: timedelta
    standard: timedelta

    @property
    def balance(self) -> timedelta:
        return self.worked + self.credited - self.standard

    @property
    def remaining(self) -> timedelta:
        return self.standard - self.worked - self.credited


@dataclass(frozen=True)
class Reconciliation:
    computed: timedelta
    reported: Decimal
    gap: timedelta
    candidates: list[DayCalc]


@dataclass(frozen=True)
class Forecast:
    """Today's standing, and the time you could walk out.

    Kept apart from every balance on purpose. Today is never settled, so these
    numbers answer "when can I go home" without being allowed to move the banked
    figure, which is the one that has to stay trustworthy.
    """

    day: date
    required: timedelta
    worked_so_far: timedelta
    earlier_today: timedelta
    entry: time | None
    entry_assumed: bool
    leave_to_close_today: time | None
    leave_to_end_level: time | None
    #: Leave or sickness already credited against today's requirement.
    credited: timedelta = timedelta(0)

    @property
    def remaining(self) -> timedelta:
        return max(timedelta(0), self.required - self.worked_so_far - self.credited)

    @property
    def done_for_today(self) -> bool:
        return self.remaining == timedelta(0)


def _plus(start: time, delta: timedelta) -> time:
    """The wall-clock time `delta` after `start`, wrapping past midnight."""
    combined = datetime.combine(date(2000, 1, 1), start) + delta
    return combined.time().replace(second=0, microsecond=0)


#: Longer than any shift: an entry older than this with no exit is a forgotten
#: clock-out, not a session still running past midnight.
LONGEST_SHIFT = timedelta(hours=16)


def _wall(start: time, end: time) -> timedelta:
    """Wall-clock time from start to end, past midnight if end is earlier."""
    return (datetime.combine(date(2000, 1, 2), end)
            - datetime.combine(date(2000, 1, 2), start)) % timedelta(days=1)


def _clock_exit(record: DayRecord, entry: time) -> time | None:
    """The exit the clock has for this entry, if the punch out has synced."""
    return next((s.exit for s in record.clock if s.entry == entry and s.exit), None)


def _closed_spans(record: DayRecord) -> list[tuple[time, time]]:
    """Every finished session of the day as (start, end).

    Finished: a row with an exit, a row with a total, or a row whose exit has
    reached the clock column and not yet the report.
    """
    spans = []
    for s in record.report:
        if not s.entry:
            continue
        end = s.exit or (
            _plus(s.entry, s.total) if s.total is not None else _clock_exit(record, s.entry)
        )
        if end is not None:
            spans.append((s.entry, end))
    return spans


def _covered(record: DayRecord, entry: time) -> bool:
    """A finished session spans this moment or comes after it.

    Either way a session begun here is not the one running now: inside a
    finished one it is that session's own punch, and before one it was closed
    by it (a clock-in at 07:55 for a row reported as 08:00-17:00). Only an
    entry later than every finished session can still be going on — so an
    earlier row never hides a later clock-in.
    """
    for start, end in _closed_spans(record):
        if start > end:              # runs past midnight: it closes the whole day
            return True
        if entry < end:
            return True
    return False


def _open_entry(record: DayRecord) -> time | None:
    """When the session still running on this day began, if one is.

    דיווח is the source of truth, but the raw clock punch lands there first, so
    the clock is read too. A reported row left open whose clock punch has
    closed is over: the exit simply has not been copied across yet. Of several
    entries still open, the latest is the one running — the others were left
    open and the day went on past them.
    """
    def latest(entries) -> time | None:
        running = [e for e in entries if not _covered(record, e)]
        return max(running) if running else None

    reported = latest(
        s.entry for s in record.report
        if s.entry and not s.exit and s.total is None and not _clock_exit(record, s.entry)
    )
    if reported is not None:
        return reported
    return latest(s.entry for s in record.clock if s.entry and not s.exit)


def _synced_exits(record: DayRecord) -> timedelta:
    """Reported rows still open whose clock punch has closed: what they came to."""
    return sum(
        (_wall(s.entry, _clock_exit(record, s.entry)) for s in record.report
         if s.entry and not s.exit and s.total is None and _clock_exit(record, s.entry)),
        timedelta(0),
    )


def _still_running(record: DayRecord, now: datetime, today: DayRecord | None = None) -> bool:
    """Whether this day's session is going on now, past midnight into the next.

    Not once today has an entry of its own before now: whoever clocked in this
    morning finished last night's shift first.
    """
    entry = _open_entry(record)
    if entry is None:
        return False
    if today is not None and any(
        s.entry is not None and s.entry <= now.time() for s in [*today.report, *today.clock]
    ):
        return False
    elapsed = now - datetime.combine(record.date, entry)
    return timedelta(0) <= elapsed <= LONGEST_SHIFT


def _forecast(day: "DayCalc", now, banked, assumed_entry):
    record, standard = day.record, day.standard
    # Today's finished work and its leave, counted exactly as for any other day,
    # plus rows whose exit has reached the clock column but not the report.
    closed, credited = day.worked + _synced_exits(record), day.credited

    real_entry = _open_entry(record)
    # A supplied entry that a finished session spans or follows would count
    # those hours twice; a real punch, or a finished session, wins over it.
    if real_entry is None and assumed_entry is not None and _covered(record, assumed_entry):
        assumed_entry = None
    entry, assumed = (
        (real_entry, False) if real_entry else (assumed_entry, assumed_entry is not None)
    )

    worked = closed
    if entry is not None:
        # The punch belongs to the record's day, which is not always the day the
        # clock is on — a pretend date, or a run just after midnight.
        started = datetime.combine(record.date, entry)
        if started <= now:
            worked += now - started

    def leave_after(needed: timedelta) -> time | None:
        if entry is None:
            return None
        # Measured from the entry, so the answer does not drift as the day runs.
        return _plus(entry, max(timedelta(0), needed - closed - credited))

    return Forecast(
        day=record.date,
        required=standard,
        worked_so_far=worked,
        earlier_today=closed,
        entry=entry,
        entry_assumed=assumed,
        leave_to_close_today=leave_after(standard),
        # Only a surplus can be spent; with a deficit there is no earlier
        # time that ends the day level, and the arithmetic would wrap past
        # midnight into a time that looks like one.
        leave_to_end_level=(
            leave_after(max(timedelta(0), standard - banked))
            if banked > timedelta(0) else None
        ),
        credited=credited,
    )


@dataclass
class Analysis:
    report: MonthReport
    days: list[DayCalc]
    today: date
    #: The moment this analysis was made, for anything still running.
    clock: datetime
    settled_through: date
    now: PeriodTotals
    week: PeriodTotals
    month: PeriodTotals
    week_start: date
    week_end: date
    week_days: list[date]
    week_in_scope: bool
    issues: list[Issue] = field(default_factory=list)
    reconciliation: Reconciliation | None = None
    forecast: Forecast | None = None
    #: Every day of the current week, from whichever month's page holds it.
    week_rows: list[DayCalc] = field(default_factory=list)

    @property
    def live_balance(self) -> timedelta:
        """The balance if you stopped this minute.

        The banked figure stops at the last settled day, so on a working
        afternoon it answers a question nobody asked: "+5.00" while today is
        2.00 short reads as being 5.00 up when walking out now leaves 3.00.

        Today is still excluded from every settled total — counting an
        unfinished day's requirement there is what invents a deficit. Here it is
        counted on purpose, and said to be conditional.
        """
        f = self.forecast
        if f is None:
            return self.now.balance
        return self.now.balance + f.worked_so_far + f.credited - f.required

    @property
    def year(self) -> int:
        return self.report.year

    @property
    def month_number(self) -> int:
        return self.report.month


def _sunday_of(day: date) -> date:
    """Israeli week starts on Sunday."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _segment_duration(entry, exit_, total) -> timedelta | None:
    if total is not None:
        return total
    if entry is None or exit_ is None:
        return None
    start = timedelta(hours=entry.hour, minutes=entry.minute)
    end = timedelta(hours=exit_.hour, minutes=exit_.minute)
    if end < start:                       # crossed midnight
        end += timedelta(days=1)
    return end - start


def _clock_duration(record: DayRecord) -> timedelta | None:
    complete = [s for s in record.clock if s.entry and s.exit]
    if not complete:
        return None
    total = timedelta(0)
    for s in complete:
        total += _segment_duration(s.entry, s.exit, None) or timedelta(0)
    return total


def _hilan_standard(record: DayRecord) -> tuple[timedelta | None, bool]:
    """The day's requirement as Hilan states it, and whether its rows conflict.

    Rows that disagree with each other cannot both be the day's requirement, so
    that leaves the rule in charge — and gets said out loud, because a day whose
    own rows contradict each other is worth a look either way.
    """
    stated = {s.standard for s in record.report if s.standard is not None}
    stated |= set(record.idle_standards)
    if not stated:
        return None, False
    if len(stated) > 1:
        return None, True
    return _minutes(stated.pop()), False


def _minutes(value: Decimal) -> timedelta:
    """Decimal hours as whole minutes, the unit Hilan counts in (8.33 is 8:20)."""
    return timedelta(minutes=int((value * 60).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def _day_calc(record: DayRecord, specials, credit_absences: bool) -> DayCalc:
    by_rule = daily_standard(record.date, specials.get(record.date))
    stated, conflict = _hilan_standard(record)
    # Hilan states the requirement for a day it has rows for, and it knows day
    # types the rule has never met. Where it speaks, it decides; the rule stays
    # the fallback, and any disagreement is reported rather than absorbed.
    standard = stated if stated is not None else by_rule
    rule_standard = by_rule if standard != by_rule else None
    worked = timedelta(0)
    hours_off = timedelta(0)
    day_off = timedelta(0)
    for seg in record.report:
        duration = _segment_duration(seg.entry, seg.exit, seg.total)
        if is_absence(seg.symbol):
            if not credit_absences:
                continue
            if duration:
                # Leave reported with its hours is worth those hours, not a day.
                hours_off += duration
            else:
                # Hilan's own ש. תקן for the row is the authority when present.
                day_off += (_minutes(seg.standard)
                              if seg.standard is not None else standard)
            continue
        if duration:
            worked += duration
    # An absence fills what is left of the day's requirement and no more, so a
    # half day of leave next to half a day of work cannot credit a full day.
    credited = min(hours_off + day_off, max(timedelta(0), standard - worked))
    return DayCalc(record=record, worked=worked, credited=credited,
                   standard=standard, rule_standard=rule_standard,
                   stated_conflict=conflict)


def _collect_issues(
    days: list[DayCalc], settled_through: date, mismatch_threshold: timedelta,
    today: date,
) -> list[Issue]:
    issues: list[Issue] = []
    for d in days:
        rec = d.record
        # A day that has not happened yet cannot be waiting for anything.
        if rec.date > today:
            continue
        # A row with a total is finished even with its exit left blank.
        open_report = any(s.entry and not s.exit and s.total is None for s in rec.report)
        # Each open clock-in on its own: a morning row finished by hand does not
        # account for an afternoon clock-in left open.
        missing_out = any(s.entry and not s.exit and not _covered(rec, s.entry) for s in rec.clock)

        # Say so rather than absorb it: either Hilan changed the day or the rule
        # has met a day type it does not know, and both are worth hearing about
        # the first time they happen.
        if d.rule_standard is not None:
            issues.append(Issue(
                IssueKind.STANDARD_MISMATCH, rec.date,
                f"Hilan requires {hrs(d.standard)} here, the rule says "
                f"{hrs(d.rule_standard)} — Hilan's figure is used",
                actionable=True,
            ))
        elif d.stated_conflict:
            issues.append(Issue(
                IssueKind.STANDARD_MISMATCH, rec.date,
                f"Hilan's rows state different requirements for this day"
                f" — the rule's {hrs(d.standard)} is used",
                actionable=True,
            ))
        # A day type this tool has never seen is counted as time off, which is
        # the safer guess — but a guess, so it is said out loud.
        for symbol in dict.fromkeys(s.symbol for s in rec.report):
            if symbol and symbol not in SYMBOLS:
                issues.append(Issue(
                    IssueKind.UNKNOWN_SYMBOL, rec.date,
                    f"day type {symbol} is not one this tool knows — counted as time off",
                    actionable=True,
                ))

        if rec.date > settled_through:
            # Punches arrive a couple of hours late, and Hilan flags the row as
            # broken in the meantime. It resolves itself, so it is news, not a task.
            if rec.error or rec.is_error_day or open_report or missing_out:
                issues.append(Issue(
                    IssueKind.PENDING_SYNC, rec.date,
                    english(rec.error) or rec.error or "not synced yet", actionable=False,
                ))
            continue

        # A turnstile entry with no exit is only a problem while nothing covers
        # it. Filling the exit in by hand is the normal fix, and Hilan accepts
        # it: the day closes, its totals include it, and it marks no error.
        # Reporting it anyway would send the reader looking for something that
        # is not there.
        if missing_out:
            issues.append(Issue(
                IssueKind.MISSING_CLOCK_OUT, rec.date,
                "no clock-out recorded", actionable=True,
            ))
        if open_report:
            issues.append(Issue(
                IssueKind.OPEN_DAY, rec.date, "reported row has no exit", actionable=True,
            ))
        if any(s.exit and not s.entry and s.total is None for s in rec.report):
            issues.append(Issue(
                IssueKind.OPEN_DAY, rec.date, "reported row has an exit but no entry",
                actionable=True,
            ))
        if rec.error or rec.is_error_day:
            issues.append(Issue(
                IssueKind.HILAN_ERROR, rec.date,
                english(rec.error) or rec.error or "Hilan flagged this day", actionable=True,
            ))
        if (
            d.standard > timedelta(0)
            and not rec.report
            and not rec.clock
            and not rec.is_absence_day
        ):
            issues.append(Issue(
                IssueKind.MISSING_DAY, rec.date, "working day with nothing reported",
                actionable=True,
            ))
        clock_total = _clock_duration(rec)
        if clock_total is not None and clock_total - d.worked > mismatch_threshold:
            issues.append(Issue(
                IssueKind.CLOCK_MISMATCH, rec.date,
                f"clock shows more than reported (by {hrs(clock_total - d.worked)})",
                actionable=True,
            ))
    return issues


def hrs(td: timedelta) -> str:
    """A duration as decimal hours, the notation Hilan itself uses.

    Printing h:mm next to Hilan's decimal figures invites reading 106:15 as
    106.15. One notation everywhere removes the trap.
    """
    return f"{hours(td)}"


def _totals(days: Sequence[DayCalc]) -> PeriodTotals:
    return PeriodTotals(
        worked=sum((d.worked for d in days), timedelta(0)),
        credited=sum((d.credited for d in days), timedelta(0)),
        standard=sum((d.standard for d in days), timedelta(0)),
    )


def analyse(
    reports: Sequence[MonthReport],
    *,
    today: date | None = None,
    now: datetime | None = None,
    assumed_entry: time | None = None,
    credit_absences: bool = True,
    mismatch_threshold: timedelta = DEFAULT_MISMATCH,
    month: tuple[int, int] | None = None,
) -> Analysis:
    if not reports:
        raise ValueError("no months to analyse")
    if month is not None and not any((r.year, r.month) == month for r in reports):
        raise ValueError(f"no page for {month[0]}-{month[1]:02d} among those given")
    # Whole minutes, the unit Hilan counts in — and the widget, so that the two
    # never disagree over a few seconds. Israel time unless told otherwise.
    now = (now or clock.now()).replace(second=0, microsecond=0)
    today = today or now.date()

    wanted = month or (today.year, today.month)
    primary = next((r for r in reports if (r.year, r.month) == wanted), reports[-1])
    specials = {
        d.date: d.special for r in reports for d in r.days if d.special is not None
    }
    by_date = {d.date: d for r in reports for d in r.days}
    all_days = [
        _day_calc(by_date[k], specials, credit_absences) for k in sorted(by_date)
    ]
    in_month = [d for d in all_days if (d.date.year, d.date.month) == (primary.year, primary.month)]

    last_dom = _calendar.monthrange(primary.year, primary.month)[1]
    month_end = date(primary.year, primary.month, last_dom)
    # A day is settled once Hilan's clock sync has moved past it, and by nothing
    # else. A closed דיווח row is not enough: a day that began with a row of
    # 06:00-06:45 would look finished by breakfast and draw its whole 9.00
    # requirement, reporting a deficit for a day not yet worked. Inventing that
    # deficit is the one thing this tool must never do, so today is never
    # counted — its hours show on their own line instead.
    sync_date = primary.clock_synced_at.date() if primary.clock_synced_at else None
    # Strictly behind both today and the clock marker. Taking today itself as a
    # candidate would let it win whenever it fell before the month being viewed,
    # settling today after all.
    reached = min(today, sync_date or today) - timedelta(days=1)
    # A shift still running past midnight has not finished its day: yesterday
    # settles once it closes, not with its hours missing and its whole
    # requirement due.
    yesterday = next((d for d in all_days if d.date == today - timedelta(days=1)), None)
    today_record = by_date.get(today)
    if yesterday is not None and reached >= yesterday.date and _still_running(
        yesterday.record, now, today_record
    ):
        reached = yesterday.date - timedelta(days=1)
    settled = min(month_end, reached)

    settled_days = [d for d in in_month if d.date <= settled]
    banked = _totals(settled_days)
    # A settled day the page has no row for still asks what the rule says, as it
    # does in the month's total: dropping it would put BANKED ahead by a day.
    unlisted = [
        day for day in (date(primary.year, primary.month, n) for n in range(1, last_dom + 1))
        if day <= settled and day not in by_date
    ]
    if unlisted:
        banked = PeriodTotals(
            worked=banked.worked, credited=banked.credited,
            standard=banked.standard + sum(
                (daily_standard(day, specials.get(day)) for day in unlisted), timedelta(0)),
        )
    # The month asks what Hilan says each day asks, as the balance does; the
    # rule only for a day the page has no row for.
    stated_by_day = {d.date: d.standard for d in in_month}
    month_totals = PeriodTotals(
        worked=sum((d.worked for d in in_month), timedelta(0)),
        credited=sum((d.credited for d in in_month), timedelta(0)),
        standard=sum(
            (stated_by_day.get(day, daily_standard(day, specials.get(day)))
             for day in (date(primary.year, primary.month, n) for n in range(1, last_dom + 1))),
            timedelta(0),
        ),
    )

    week_start = _sunday_of(today)
    week_end = week_start + timedelta(days=6)
    week_days = [
        d for d in all_days if week_start <= d.date <= min(week_end, settled)
    ]
    week_in_scope = any(
        (r.year, r.month) in {(week_start.year, week_start.month), (week_end.year, week_end.month)}
        for r in reports
    )

    issues = _collect_issues(in_month, settled, mismatch_threshold, today)
    issues += [
        Issue(IssueKind.MISSING_DAY, day, "no row on Hilan's page for this working day",
              actionable=True)
        for day in unlisted if daily_standard(day, specials.get(day)) > timedelta(0)
    ]
    issues.sort(key=lambda i: i.date)
    today_calc = next((d for d in in_month if d.date == today), None)
    reconciliation = _reconcile(
        primary, in_month, month_totals,
        today_calc.worked if today_calc else timedelta(0),
    )

    # Today asks what Hilan says it asks, like every other day: the rule only
    # where Hilan says nothing.
    today_day = next((d for d in all_days if d.date == today), None)
    forecast = (
        _forecast(today_day, now, banked.balance, assumed_entry)
        if today_day is not None
        else None
    )

    return Analysis(
        report=primary,
        days=in_month, today=today, clock=now, settled_through=settled,
        now=banked, week=_totals(week_days), month=month_totals,
        week_start=week_start, week_end=week_end,
        week_days=[d.date for d in week_days], week_in_scope=week_in_scope,
        issues=issues, forecast=forecast, reconciliation=reconciliation,
        week_rows=[d for d in all_days if week_start <= d.date <= week_end],
    )


def _reconcile(
    report: MonthReport, days: list[DayCalc], totals: PeriodTotals,
    today_worked: timedelta = timedelta(0),
) -> Reconciliation | None:
    """Compare our own sum with Hilan's ``שעות בפועל``.

    Only meaningful for the current payroll month: the legend panel never
    follows the month being browsed.

    And only past today's own hours. The legend is a snapshot taken at some
    moment Hilan does not disclose, while today is still moving: the rows can
    hold two sessions of which בפועל has taken in only one, putting the live
    total exactly one punch above it. A difference no larger than today's hours
    is evidence of nothing — those are the hours the summary may not have seen.
    """
    if not report.totals_match_month or report.totals is None:
        return None
    reported = report.totals.actual
    if reported is None:
        return None
    gap_hours = hours(totals.worked) - reported
    gap = timedelta(minutes=round(float(gap_hours) * 60))
    if gap == timedelta(0):
        return None
    # Only our side can be ahead because of today: Hilan reporting more is not
    # explained by hours it has not seen yet.
    if timedelta(0) < gap <= today_worked:
        return None
    candidates = [d for d in days if d.worked == gap and d.worked > timedelta(0)]
    return Reconciliation(
        computed=totals.worked, reported=reported, gap=gap, candidates=candidates
    )
