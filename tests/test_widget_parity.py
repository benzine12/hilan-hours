# -*- coding: utf-8 -*-
"""The Scriptable widget must agree with this code, checked on the code it ships.

The widget is a second implementation of the whole calculation in JavaScript,
and a second copy drifts. A copy of the logic inside a test file would prove
nothing about the file that runs on the phone.

So this loads ios/HilanWidget.js itself, runs its parseAndAnalyse and toSpoken
on many pages at many moments — in one Node process — and compares every figure
the widget shows, and the sentence it gives Siri, with what Python computes:

- both made-up months, every day, every hour;
- 150 random months (a fixed seed, so a failure can be replayed)
  with sessions past midnight, open entries, covered and stray punches, absences
  beside work, holidays and eves, validation messages, requirements Hilan
  states that the rule would not, rows that disagree with each other, and a
  clock marker before, inside or past the month;
- a page carrying two months' grids.
"""
import calendar as cal
import json
import random
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from capture import CAPTURES, LEGEND, WHO
from conftest import build_html
from hilan.calc import analyse, hrs
from hilan.parser import parse_month
from hilan.say import spoken

ROOT = Path(__file__).resolve().parent.parent
WIDGET = ROOT / "ios" / "HilanWidget.js"

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const [widgetPath, casesPath] = process.argv.slice(1);   // node -e puts arguments from index 1
const code = fs.readFileSync(widgetPath, 'utf-8').replace(/^await run\(\);\s*$/m, '');
const ctx = { console: { log() {}, error() {} }, config: {},
  FileManager: { local: () => ({ documentsDirectory: () => '/tmp', joinPath: (a, b) => a + '/' + b,
    fileExists: () => false, readString: () => '', writeString: () => {} }) },
  Keychain: {}, Script: {}, Request: class {}, Alert: class {}, ListWidget: class {},
  Color: class {}, Font: {} };
vm.createContext(ctx);
vm.runInContext(code, ctx);
const out = [];
for (const c of JSON.parse(fs.readFileSync(casesPath, 'utf-8'))) {
  const html = fs.readFileSync(c.page, 'utf-8');
  for (const [y, m, d, hh, mm] of c.moments) {
    try {
      const a = ctx.parseAndAnalyse(html, new Date(y, m - 1, d, hh, mm));
      out.push({
        settled_through: a.settledThrough,
        banked_worked: a.banked.worked, banked_credited: a.banked.credited,
        banked_standard: a.banked.standard, banked_balance: a.banked.balance,
        month_worked: a.monthTotals.worked, month_standard: a.monthTotals.standard,
        month_remaining: a.monthTotals.remaining,
        live_balance: a.live.balance,
        today_worked: a.today.workedSoFar, today_required: a.today.standard,
        leave: a.today.leaveToClose, done: a.today.done,
        spoken: ctx.toSpoken(a),
      });
    } catch (e) { out.push({ error: String(e && e.message || e) }); }
  }
}
process.stdout.write(JSON.stringify(out));
"""

# Found once, by full path: on Windows a bare "node" is not always resolved.
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _period_fields(a) -> dict:
    return {
        "settled_through": a.settled_through.isoformat(),
        "banked_worked": hrs(a.now.worked), "banked_credited": hrs(a.now.credited),
        "banked_standard": hrs(a.now.standard), "banked_balance": hrs(a.now.balance),
        "month_worked": hrs(a.month.worked), "month_standard": hrs(a.month.standard),
        "month_remaining": hrs(a.month.remaining),
    }


def python(report, when: datetime) -> dict:
    a = analyse([report], today=when.date(), now=when)
    f = a.forecast
    if f is None:                            # no row for today on the page
        return {
            **_period_fields(a),
            "live_balance": hrs(a.live_balance),
            "today_worked": "0.00", "today_required": "0.00",
            "leave": None, "done": True, "spoken": spoken(a),
        }
    return {
        "settled_through": a.settled_through.isoformat(),
        "banked_worked": hrs(a.now.worked), "banked_credited": hrs(a.now.credited),
        "banked_standard": hrs(a.now.standard), "banked_balance": hrs(a.now.balance),
        "month_worked": hrs(a.month.worked), "month_standard": hrs(a.month.standard),
        "month_remaining": hrs(a.month.remaining),
        "live_balance": hrs(a.live_balance),
        "today_worked": hrs(f.worked_so_far), "today_required": hrs(f.required),
        # The widget shows no leave time once the day is made; Python keeps the
        # value and hides it at render time. Compare what the person sees.
        "leave": (f.leave_to_close_today.strftime("%H:%M")
                  if f.leave_to_close_today and not f.done_for_today else None),
        "done": f.done_for_today,
        "spoken": spoken(a),
    }


def compare(tmp_path, pages):
    """Every (page, moment) through both sides; the moments that differ."""
    cases = []
    for i, (html, moments) in enumerate(pages):
        page = tmp_path / f"page{i}.html"
        page.write_text(html, encoding="utf-8")
        cases.append({"page": str(page), "moments": [
            [w.year, w.month, w.day, w.hour, w.minute] for w in moments]})
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    done = subprocess.run([NODE, "-e", RUNNER, str(WIDGET), str(tmp_path / "cases.json")],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    widget = iter(json.loads(done.stdout))
    differing = []
    for html, moments in pages:
        report = parse_month(html)           # analyse only reads it
        for when in moments:
            expected, got = python(report, when), next(widget)
            diff = {k: (expected.get(k), got.get(k)) for k in expected.keys() | got.keys()
                    if str(expected.get(k)) != str(got.get(k))}
            if diff:
                differing.append(f"{when:%Y-%m-%d %H:%M}: {diff}")
    return differing


# --- random months -----------------------------------------------------------

WEEKDAY = {6: "א", 0: "ב", 1: "ג", 2: "ד", 3: "ה", 4: "ו", 5: "שבת"}
WORK = ["נוכחות", "נכח", "עבודה מהבית", "עבודה במילואים", "מפגש עובד מנהל",
        "השתלמות"]                      # the last is a day type nothing knows
ABSENT = ["חופשה", "מחלה", "מילואים", "חופשה בגין חג"]
SPECIALS = ["חג", "ערב חג", 'ערב יוה"כ', "חול המועד", "ערב ראש השנה"]
ERRORS = ["קיים דיווח ללא פרויקט באותה השורה", "חסרה יציאה"]
BLANK = "&nbsp;"


def _hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def random_month(rng: random.Random):
    """A month with every kind of day a real page throws up, and some it rarely does."""
    y, m = rng.choice([2025, 2026, 2027]), rng.randint(1, 12)
    last = cal.monthrange(y, m)[1]
    rows, calendar = [], []
    for n, dom in enumerate(range(1, last + 1)):
        day = date(y, m, dom)
        roll = rng.random()
        special = (rng.choice(SPECIALS) if roll < 0.08
                   else rng.choice(ERRORS) if roll < 0.11 else None)
        report, clock = [], []
        kind = rng.random()
        if kind < (0.12 if day.weekday() in (4, 5) else 0.75):          # worked
            for _ in range(rng.choice([1, 1, 1, 2, 2, 3])):
                start, length = rng.randint(0, 23 * 60), rng.randint(5, 13 * 60)
                has_exit = rng.random() > 0.08                            # an open entry
                total = _hhmm(length) if has_exit and rng.random() > 0.15 else BLANK
                standard = rng.choice(["9.00", "9.00", "8.50", "4.00", "", "8.00", "8.33"])
                report.append((_hhmm(start), _hhmm(start + length) if has_exit else "",
                               total, standard, "", rng.choice(WORK)))
            for seg in report:
                if rng.random() < 0.7:
                    clock.append((seg[0] if rng.random() > 0.1 else BLANK,
                                  seg[1] if seg[1] and rng.random() > 0.15 else BLANK))
            if rng.random() < 0.1:                                        # a stray punch
                clock.append((_hhmm(rng.randint(0, 23 * 60)), BLANK))
        elif kind < 0.85:                                                 # an absence
            if rng.random() < 0.25:                                       # with its hours
                s = rng.randint(7 * 60, 12 * 60)
                report.append((_hhmm(s), _hhmm(s + 180), rng.choice(["03:00", BLANK]),
                               "9.00", "", rng.choice(ABSENT)))
            else:
                report.append(("", "", BLANK, rng.choice(["9.00", "8.50", "4.00", "7.42", ""]),
                               "", rng.choice(ABSENT)))
            if rng.random() < 0.3:                                        # beside some work
                s = rng.randint(6 * 60, 12 * 60)
                report.append((_hhmm(s), _hhmm(s + 240), "04:00", "9.00", "", rng.choice(WORK)))
        elif kind < 0.9:                                                  # a row reporting nothing
            report.append(("", "", BLANK, rng.choice(["9.00", "8.50", "4.00", "0.00", ""]), "", ""))
        if rng.random() < 0.02:                                           # a missing row
            continue
        rows.append((n, f"{dom:02d}/{m:02d}", WEEKDAY[day.weekday()], special,
                     clock or [(BLANK, BLANK)], report or [("", "", BLANK, "", "", "")]))
        classes = ["cHD CSD" if day.weekday() == 5 or special in SPECIALS else "cDIES CSD"]
        if special in ERRORS:
            classes.append("cED")
        calendar.append(((day - date(2000, 1, 1)).days, " ".join(classes),
                         special if special in SPECIALS else ""))
    sync_day = date(y, m, rng.randint(1, last)) + timedelta(days=rng.choice([0, 0, 1, -1, 5]))
    sync = (f"נתוני שעון מעודכנים לתאריך {sync_day:%d/%m/%Y} "
            f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}" if rng.random() > 0.05 else "")
    html = build_html({
        "gid": f"ctl00_mp_RG_Days_100012345_{y}_{m:02d}", "who": WHO,
        "currentMonth": f"01/{m:02d}/{y}", "sync": sync, "legend": LEGEND,
        "calendar": calendar, "rows": rows,
    })
    moments = [datetime(y, m, rng.randint(1, last), rng.randint(0, 23), rng.randint(0, 59))
               for _ in range(12)]
    return html, moments


def _every_hour(year, month):
    last = cal.monthrange(year, month)[1]
    return [datetime(year, month, d, h, 7) for d in range(1, last + 1) for h in range(24)]


class TestTheWidgetAgreesWithPython:
    def test_on_the_made_up_months(self, tmp_path):
        pages = [(build_html(CAPTURES[key]), _every_hour(*map(int, key.split("-"))))
                 for key in CAPTURES]
        differing = compare(tmp_path, pages)
        assert not differing, "\n".join(differing[:10])

    def test_on_random_months(self, tmp_path):
        rng = random.Random(20260930)
        differing = compare(tmp_path, [random_month(rng) for _ in range(150)])
        assert not differing, "\n".join(differing[:10])

    def test_on_a_page_with_two_months(self, tmp_path, september_html, august_html):
        """The widget, like the parser, picks the grid the page says it shows."""
        body = august_html.split("<body>")[1].split("</body>")[0]
        page = september_html.replace("</body>", body + "</body>")
        differing = compare(tmp_path, [(page, _every_hour(2026, 9)[::7])])
        assert not differing, "\n".join(differing[:10])
