# -*- coding: utf-8 -*-
"""English wording for Hilan's Hebrew, and Hebrew that reads the right way.

Two problems that belong in one place. Hilan speaks Hebrew, and the terminals
this tool runs in do not implement the Unicode bidirectional algorithm: xterm.js
(and the editors and desktop apps built on it), Terminal.app and iTerm2 all
paint characters in logical order, so a Hebrew word arrives spelled backwards.

Translating the vocabulary removes most of the Hebrew outright. What is left — a
name, a free-text comment somebody typed — is reordered so it reads correctly.
Every entry below is a term Hilan uses on its pages; none is guessed,
because a confidently wrong translation of a leave type would be worse than
showing the Hebrew.

Set HILAN_BIDI=logical for a terminal that does its own bidi.
"""
from __future__ import annotations

import os
import re

#: Attendance symbols from the day rows.
SYMBOLS = {
    "נוכחות": "present",
    "נכח": "present",
    "עבודה מהבית": "work from home",
    "עבודה במילואים": "reserve duty work",
    "מפגש עובד מנהל": "employee-manager meeting",
    "חופשה": "vacation",
    "מחלה": "sick leave",
    "מילואים": "reserve duty",
}

#: Calendar labels that change the day's requirement.
SPECIALS = {
    "חג": "holiday",
    "ערב חג": "holiday eve",
    "חול המועד": "mid-festival",
    'ערב יוה"כ': "Yom Kippur eve",
    "ערב ראש השנה": "Rosh Hashanah eve",
    "שבת": "Saturday",
}

#: Validation messages Hilan puts in the day's own cell.
MESSAGES = {
    "קיים דיווח ללא פרויקט באותה השורה": "a row is reported without a project",
}

_VOCABULARY = {**SYMBOLS, **SPECIALS, **MESSAGES}

_HEB = "֐-׿יִ-ﭏ"
_HEBREW = re.compile(f"[{_HEB}]")

# A right-to-left span begins and ends on a Hebrew letter and may carry anything
# in between. Requiring Hebrew at both ends is what keeps a trailing English word
# or number — which the terminal already places correctly — from being dragged in.
_SPAN = re.compile(f"[{_HEB}](?:[^{_HEB}]*[{_HEB}])*")

# Inside a reversed span these read left to right again.
# Runs that read left to right inside a right-to-left span, found in logical
# order as the Unicode bidi algorithm finds them: Latin words and whatever
# numbers follow them, across spaces and punctuation, are one run ("worked
# 9:10", "hi there"); a number with no Latin word before it stands alone, so
# "12 34" is shown right to left like the Hebrew around it.
_LATIN_RUN = re.compile(r"[0-9]*[A-Za-z][0-9A-Za-z]*(?:(?:[.:,/]|\s+)(?=[0-9A-Za-z])[0-9A-Za-z]+)*")
_NUMBER = re.compile(r"[0-9]+(?:[.:,/][0-9]+)*")


def english(text: str | None) -> str | None:
    """The English for a Hilan term, or None if we have not seen that term."""
    if not text:
        return None
    return _VOCABULARY.get(text.strip())



def visual(text: str | None) -> str:
    """Reorder Hebrew so it reads correctly on a terminal without bidi."""
    if not text or os.environ.get("HILAN_BIDI") == "logical":
        return text or ""
    if not _HEBREW.search(text):
        return text
    return _SPAN.sub(lambda m: _flip(m.group(0)), text)


def _flip(span: str) -> str:
    """The span in display order: reversed, with its left-to-right runs kept whole."""
    tokens, i = [], 0
    while i < len(span):
        run = _LATIN_RUN.match(span, i) or _NUMBER.match(span, i)
        tokens.append(run.group(0) if run else span[i])
        i = run.end() if run else i + 1
    return "".join(reversed(tokens))


def display(text: str | None) -> str | None:
    """What to print for a value that may be Hebrew: English if we know it."""
    if text is None:
        return None
    return english(text) or visual(text)
