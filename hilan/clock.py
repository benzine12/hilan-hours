# -*- coding: utf-8 -*-
"""Hilan's clock: Israel time, wherever this runs.

Hilan counts days and punches in Israel time. A server in UTC, or a laptop
abroad, would otherwise put "today" on the wrong date and add or drop hours
from a running session.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ISRAEL = ZoneInfo("Asia/Jerusalem")


def now() -> datetime:
    """The wall-clock time in Israel, to the minute, without a timezone attached."""
    return datetime.now(ISRAEL).replace(tzinfo=None, second=0, microsecond=0)


def today() -> date:
    return now().date()
