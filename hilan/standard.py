"""How many hours a given day is *supposed* to hold.

The rule below reproduces Hilan's own ``תקן`` and ``תקן עד היום`` for
September 2026 exactly (173.00 and 119.50), which is what makes it trustworthy
enough to compute a balance from.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

# Labels Hilan puts in the day's "special" row.
HOLIDAY = "חג"
EREV_CHAG = "ערב חג"
CHOL_HAMOED = "חול המועד"

FULL_DAY = timedelta(hours=9)
SHORT_DAY = timedelta(hours=8, minutes=30)  # Thursday
EREV_DAY = timedelta(hours=4)

_FRIDAY, _SATURDAY, _THURSDAY = 5, 6, 4  # isoweekday()


def _is_erev(special: str) -> bool:
    """Any 'eve of a holiday' label, including ערב יוה"כ and ערב ראש השנה."""
    return special.startswith("ערב")


def daily_standard(day: date, special: str | None) -> timedelta:
    """Required hours for one day.

    Precedence matters: the weekend wins over a holiday label (an ערב חג that
    lands on a Friday is still simply a day off), a full holiday zeroes the
    day, an eve is a half day, and חול המועד is an ordinary workday.
    """
    weekday = day.isoweekday()
    if weekday in (_FRIDAY, _SATURDAY):
        return timedelta(0)
    if special:
        if special == HOLIDAY:
            return timedelta(0)
        if _is_erev(special):
            return EREV_DAY
    return SHORT_DAY if weekday == _THURSDAY else FULL_DAY


def month_standard(year: int, month: int, specials: Mapping[date, str]) -> timedelta:
    """Required hours for the whole month."""
    last = calendar.monthrange(year, month)[1]
    total = timedelta(0)
    for dom in range(1, last + 1):
        day = date(year, month, dom)
        total += daily_standard(day, specials.get(day))
    return total


def standard_through(
    year: int, month: int, through: date, specials: Mapping[date, str]
) -> timedelta:
    """Required hours from the 1st of the month up to and including ``through``.

    ``through`` outside the month is clamped: an earlier date yields zero, a
    later one yields the whole month. ``specials`` may span other months;
    entries outside this one are ignored.
    """
    last_dom = calendar.monthrange(year, month)[1]
    if through < date(year, month, 1):
        return timedelta(0)
    end_dom = last_dom if through >= date(year, month, last_dom) else through.day
    total = timedelta(0)
    for dom in range(1, end_dom + 1):
        day = date(year, month, dom)
        total += daily_standard(day, specials.get(day))
    return total


def hours(td: timedelta) -> Decimal:
    """Duration as decimal hours, the way Hilan prints them (2 places)."""
    return (Decimal(td.total_seconds()) / Decimal(3600)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
