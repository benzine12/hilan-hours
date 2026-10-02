"""Conversion between calendar dates and Hilan's day serials.

Hilan's attendance calendar tags every day cell with a ``days`` attribute and
submits the selected ones through the hidden ``__calendarSelectedDays`` field.
The serial is a plain day offset from 2000-01-01, verified against the live
page: 2026-09-01 is 9740 and 2026-09-22 is 9761.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

EPOCH = date(2000, 1, 1)


def date_to_serial(day: date) -> int:
    return (day - EPOCH).days


def serial_to_date(serial: int) -> date:
    return EPOCH + timedelta(days=serial)


def month_serials(year: int, month: int) -> list[int]:
    """Serials for every day of the month, in order.

    This is what a full-month fetch puts into ``__calendarSelectedDays``.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1..12, got {month}")
    days_in_month = calendar.monthrange(year, month)[1]
    first = date_to_serial(date(year, month, 1))
    return list(range(first, first + days_in_month))


def selected_days_field(year: int, month: int) -> str:
    """The exact comma-separated value Hilan expects."""
    return ",".join(str(s) for s in month_serials(year, month))
