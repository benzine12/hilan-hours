# -*- coding: utf-8 -*-
"""One sentence, for Siri to read out and a notification to carry.

Neither can show a table. A macOS Shortcut can run `hilan --say` and speak the
result or show it as a notification, so it has to be a whole thought in plain
words — and hours said the way a person says them. "You are 4.25 up" is a column heading read aloud; four
hours fifteen is what someone would actually say.
"""
from __future__ import annotations

from datetime import timedelta

from .calc import Analysis

#: A notification shows about this much before it is cut off.
LIMIT = 160


def _hm(td: timedelta) -> str:
    """A duration in words: "9 hours", "4 hours 12", "35 minutes"."""
    minutes = abs(int(td.total_seconds())) // 60
    hours, rest = divmod(minutes, 60)
    if not hours:
        return "1 minute" if rest == 1 else f"{rest} minutes"
    plural = "hour" if hours == 1 else "hours"
    return f"{hours} {plural}" if not rest else f"{hours} {plural} {rest}"


def spoken(analysis: Analysis) -> str:
    """The whole standing in one sentence a person could say out loud."""
    # The live figure, not the settled one: this is asked in the middle of a
    # day, and "you are 5 hours up" while today is 2 hours short answers a
    # question nobody put.
    balance = analysis.live_balance
    direction = "up" if balance >= timedelta(0) else "down"
    parts = [f"You are {_hm(balance)} {direction} right now"]

    f = analysis.forecast
    if f is None:
        return parts[0] + "."

    if f.done_for_today:
        parts.append(f"{_hm(f.worked_so_far)} today, done for today")
    elif f.entry is None:
        parts.append(f"{_hm(f.worked_so_far)} today, not clocked in")
    else:
        parts.append(f"{_hm(f.worked_so_far)} today")
        if f.leave_to_close_today:
            parts.append(
                f"you can leave at {f.leave_to_close_today.strftime('%H:%M')}"
            )

    said = ", ".join(parts[:2])
    if len(parts) > 2:
        said += f". {parts[2][0].upper()}{parts[2][1:]}"
    return said + "."
