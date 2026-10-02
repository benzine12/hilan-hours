# hilan-hours

## Contents

- [Overview](#overview)
- [Install](#install)
- [Commands](#commands)
- [Signing in](#signing-in)
- [The Scriptable widget](#the-scriptable-widget)
- [Glossary](#glossary)
- [Notation](#notation)
- [Hebrew and language](#hebrew-and-language)
- [How the balance is computed](#how-the-balance-is-computed)
- [When can I leave today](#when-can-i-leave-today)
- [History and logs](#history-and-logs)
- [When the clocks move](#when-the-clocks-move)
- [Read-only](#read-only)
- [Hilan API map](#hilan-api-map)
  - [Login](#login)
  - [Attendance data](#attendance-data)
  - [Parsing the page](#parsing-the-page)
- [Development](#development)
- [License](#license)

## Overview

An honest count of your hours on top of Hilan.

Hilan's own summary can disagree with the days it shows you. In the example month
the tests use, it reports `שעות בפועל 97.25` while the daily totals in the same
table add up to **106.25** — a gap of exactly **9.00**, all of it on 08/09, the
one day with no clock-out. This tool counts the hours itself, compares the result with Hilan's
summary, and names the days that eat the difference.

It only ever reads: a guard on every request makes saving anything in Hilan
impossible (see [Read-only](#read-only)). It is an unofficial personal tool, not
affiliated with or endorsed by Hilan, and it talks to Hilan's site the way your
browser does, with your own credentials.

```
$ hilan

  Hilan · September 2026
  ──────────────────────────────────────────────────────────────────────────

  NOW      +2.08   stopping now
  BANKED   +4.75   worked 106.25 + credited 9.00 · required 110.50 · to 21/09
  MONTH    57.75   left of 173.00
  TODAY    22/09   08:15–…   6.33  running
           6.33 of 9.00 · 2.67 to go · leave 17:15 to close · 12:30 to end level

  Hilan reports:  actual 97.25 · productive 96.94 · gap -9.00
    └─ 08/09 · 9.00

  Clock synced through 22/09 16:40

  Waiting to sync (fixes itself)
    22/09  a row is reported without a project
```

(This screen and the next are produced from the made-up month in `tests/capture.py`,
with `--today 2026-09-22 --now 14:35`.)

**NOW** is what the bank holds this minute: the settled figure, plus what today
has done, less what today asks. It moves while you work. **BANKED** stops at the
last settled day and does not move at all.

Both are true and neither replaces the other. +4.75 banked while today is 2.67
short would read as being 4.75 up, when walking out now leaves 2.08 — so the
live one leads and says it is conditional. When the two are equal — nothing
worked or asked today, or today worked exactly to what it asks — only one line
appears.

`hilan --brief` cuts it to the two numbers that matter most — hours done and
hours banked — with one line pointing back here if a day needs attention:

```
$ hilan --brief

Hilan · September 2026

  WORKED   106.25   recorded this month · 6.33 running
  BANKED    +2.08   right now · 4.75 settled to 21/09
```

## Install

Python 3.11 or newer, on macOS, Linux or Windows. With
[pipx](https://pipx.pypa.io), which puts `hilan` on the path in its own
environment, the same two commands work everywhere:

```bash
pipx install git+https://github.com/benzine12/hilan-hours.git
hilan login
```

Without pipx, `python -m pip install git+https://github.com/benzine12/hilan-hours.git`
does the same inside whatever environment you choose.

`hilan login` asks three things once: your company's Hilan address (the site
you log in at, like `https://yourcompany.net.hilan.co.il`), your employee number
and your password. The address and number go to the config directory, the
password to the system's password store — the Keychain on macOS, Credential
Manager on Windows, Secret Service (GNOME Keyring, KWallet) on Linux.

## Commands

| Command | What it does |
|---|---|
| `hilan` | Balance, requirement, leave forecast, problem days |
| `hilan --brief` | Just two numbers: hours done and hours banked |
| `hilan --days` | The same, plus a table of every day |
| `hilan --week` | Day table for the current week only |
| `hilan --month 2026-08` | A different month |
| `hilan --json` | Machine-readable output |
| `hilan --say` | One sentence, for Siri or a notification |
| `hilan --from-file page.html` | Offline, from saved HTML; may be repeated |
| `hilan --strict-attendance` | Do not credit leave or sick days |
| `hilan --in 08:30` | Clock-in Hilan has not synced yet, for the leave forecast |
| `hilan --today 2026-09-22` | Treat this date as today (for checking) |
| `hilan --now 14:00` | Treat this as the current time (for checking) |
| `hilan history` | Totals per month from every run so far |
| `hilan --verbose` | Also print the log to the terminal |
| `hilan login` | Store the Hilan address and credentials; `--user` gives the employee number, `--stdin` reads the password from standard input |
| `hilan fetch --month 2026-09 --out dir/` | Download a month's HTML without parsing it |

## Signing in

Every company has its own Hilan site, so the address is configuration: the first
of `HILAN_URL` in the environment, `HILAN_URL` in the `.env` file, and what
`hilan login` saved. It must be an `https://…hilan.co.il` address — the password
is sent there, so anything else is refused rather than tried. Pasting the whole
URL from the browser is fine; only the host is kept.

`hilan login` is needed once. After that the session lives in `cookies.json` in the
config directory and the password in the password store, and the tool signs in
again by itself whenever the session lapses — including when it
lapses between checking the session and making the request, which is a retry
rather than an error. Logging in rarely is also what keeps Hilan's SMS challenge
away, since it remembers the client.

It signs in only when the session is really gone — Hilan answers 401 or 403, or
sends the request back to the login page. A server error or a timeout is
reported as one, not answered with the password.

If Hilan turns the password down, the tool stops sending it. Every run would
otherwise try it again, and a stored password that has since been changed is
how an account gets locked out. It says so and asks for `hilan login`, which
stores the new password and lifts the stop. A refusal of the password typed
into `hilan login` sets the same stop, and so does a 401 or a JSON answer
without `IsFail: false`: a login is taken as accepted only when Hilan says so.
A 403 from Hilan's firewall, a timeout or a page that is not JSON never judged
the password, so they are reported and not counted as a refusal. A verification code asked for on a run with
no terminal (a scheduled one) stops it too, rather than sending one SMS per
run; `hilan login` in a terminal asks for the code and lifts the stop.

Where there is no password store — a Linux server with no desktop session, say —
the password goes to a `.env` file in the config directory instead, plain text
readable only by you (`0600`). Windows has no such modes: there the file is as
private as the folder it is in, which for the default `%APPDATA%` is your
profile — keep `HILAN_HOME` and `HILAN_ENV` inside it. That is
weaker than a real store, and is used only where there is none — once the store
takes the password, the copy in the file is removed. An exported
`HILAN_PASSWORD` always wins over what is stored, `HILAN_USER` gives the
employee number the same way, and `HILAN_ENV` points at a different `.env`.

The config directory is `~/.config/hilan-hours` on macOS and Linux and
`%APPDATA%\hilan-hours` on Windows. `$XDG_CONFIG_HOME/hilan-hours` takes over on
any system where that variable is set, and `HILAN_HOME` moves it anywhere else —
but not, on macOS and Linux, into a folder that belongs to another user, which is refused: whoever
owns it could swap the files in it for links to anything of yours.

## The Scriptable widget

`ios/HilanWidget.js` runs on the iPhone as a WidgetKit widget in
[Scriptable](https://scriptable.app), so nothing opens and no Mac is needed.
It shows the live and banked balance on the home screen and lock screen, and
answers Siri through a Shortcut's "Run Script" action.

1. Create a script in Scriptable and paste the file into it.
2. Set `BASE_URL` at the top to your company's Hilan site. Until then the
   widget only says to, and sends nothing.
3. Run it once inside Scriptable: it asks for your employee number and password
   and keeps them in the iOS Keychain.
4. Add a Scriptable widget to the home or lock screen and pick the script. Every
   size works: small, medium, large and extra large on the home screen; inline,
   rectangular and circular on the lock screen.

When Hilan cannot be reached it shows the last good figure marked
`as of HH:MM`, never passed off as current, and asks iOS for a fresh one
within a quarter of an hour. Asked through Siri, it says what went wrong.

It follows the command line's rules on the network: the same read-only guard,
redirects checked hop by hop (https, your company's site, and a POST may not be
carried to another page), signing in only when the session is really gone, and
cookies kept with their paths and the site they came from, and sent the way a
browser sends them. If Hilan turns the
password down, the widget forgets it and stops trying — every refresh would
otherwise try again — until the script is run inside Scriptable, which asks for
it anew. When Hilan asks for a verification code it stops too, since every try
sends another SMS; run inside Scriptable, it asks for the code and sends it.

Scriptable's Keychain is shared by every script in the app, so any script you
run there can read the stored password. Run only scripts you trust in it.

It is a second implementation of the whole calculation in JavaScript, and a
second copy drifts — so it is checked against this one, on the file that
actually ships:

- `tests/test_widget_parity.py` runs the widget's own `parseAndAnalyse` and
  `toSpoken` in one Node process and compares every figure it shows, and what it
  gives Siri, with Python: both made-up months at every hour, 150 random months
  full of edge cases (a fixed seed, so a failure replays), and a page carrying
  two months.
- `tests/test_widget_stale.py` runs its `run()` with the network failing and
  checks a cached figure is marked with the time it was true, on the home
  screen, the lock screen and in what Siri is handed.
- `tests/test_widget_guard.py` holds its read-only guard to the same rules as
  `client.py`.
- `tests/test_widget_network.py` runs its network code against a made-up
  Scriptable and a made-up Hilan (`tests/phone.py`): when it signs in, what a
  refused password does, which redirects it follows, which cookies it sends,
  and that it reads Israel time on a phone set to any other zone.
- `tests/test_widget_config.py` checks it sends nothing until `BASE_URL` is a
  real Hilan site.

These need `node` and are skipped without it. A change to the calculation here
has to be made in the widget too, and these tests say when it has not.

## Glossary

Hilan's own words, as they appear on its pages and in this document.

| Hebrew | Meaning |
|---|---|
| `דיווח` | The reported rows for a day: entry, exit, total, day type |
| `דיווחי שעון` | Raw clock punches, before they become reported rows |
| `ש. תקן` | A day's required hours ("standard hours") |
| `תקן` / `תקן עד היום` | Required hours for the month / up to today |
| `שעות בפועל` | Hours actually worked, by Hilan's count |
| `יצרניות` | "Productive" hours, after Hilan's internal rounding |
| `נתוני שעון מעודכנים` | "Clock data updated to…", how far the punches reach |
| `חג` / `ערב חג` / `חול המועד` | Holiday / holiday eve / intermediate festival days |
| `חופשה` / `מחלה` / `מילואים` | Vacation / sick leave / reserve duty |
| `שמירה` | Save — the button the read-only guard exists to keep away from |

## Notation

Durations are decimal hours, to two places, the way Hilan writes them: `106.25`,
not `106:15`. Times of day stay as clock times (`07:55–17:05`), because that is
what they are.

A total is the rounded exact sum, not the sum of the rounded days, so adding up
rounded days by hand can land a cent away from it: three days of 1:01 show as
1.02 each, but come to 3.05, not 3.06. The total is the honest one — it is what
Hilan's `בפועל` is compared against.

Printing h:mm beside Hilan's own `119.50` would invite exactly one mistake:
reading `106:15` as 106.15. So there is one notation, the one Hilan uses, and any
number here can be compared with Hilan directly.

## Hebrew and language

Everything the tool says is English. Hilan speaks Hebrew, and the terminals this
usually runs in — Terminal.app, iTerm2, Windows Terminal, anything built on
xterm.js — do not implement the Unicode bidirectional algorithm: they paint characters in logical
order, so a Hebrew word comes out spelled backwards.

`hilan/text.py` handles both halves. Terms Hilan uses are
translated (`חופשה` → vacation, `חג` → holiday, `קיים דיווח ללא פרויקט באותה השורה`
→ "a row is reported without a project"); nothing is guessed, because a leave type
translated wrongly would be worse than showing the Hebrew. Whatever is left — your
name, a comment somebody typed — is reordered so it reads correctly, with digits
and Latin words inside a Hebrew span keeping their own direction.

If Hebrew comes out backwards anyway, your terminal does its own bidi: set
`HILAN_BIDI=logical`. `--json` always
carries the Hebrew exactly as Hilan wrote it, since programs do not care about
display order.

## How the balance is computed

Hilan counts days and hours in Israel time, so the tool does too: "today" and
"now" are read in `Asia/Jerusalem`, whatever the machine's own clock says. A
laptop abroad or a server in UTC sees the same day Hilan does.

**Where Hilan states a day's requirement, that is the figure used.** It prints
one (`ש. תקן`) on every day it has rows for — including rows that report nothing
yet, a future day or one not filled in — and it knows day types no rule of ours
has met. Today's leave forecast uses the same figure.

The rule is the fallback for the rest — weekends and holidays, which carry no
rows: **Sun–Wed 9h · Thu 8.5h · `ערב חג` 4h · `חג`/Fri/Sat 0 · `חול המועד` like an
ordinary day.** It reproduces Hilan's own figures for September 2026 exactly —
`תקן` 173.00 and `תקן עד היום` 119.50 — and is pinned by a golden test.

Any disagreement is reported rather than absorbed, and so is a day whose own rows
state requirements that contradict each other. Falling through to "an ordinary
9.00" for a day type the rule has never seen, and saying nothing, is the one
failure this tool exists to prevent.

Both sides of a balance are cut at one date, `settled_through`: **a day is settled
once Hilan's clock sync has moved past it, and by nothing else.** Punches land a
couple of hours after you leave, and Hilan says how far its clock data reaches
(`נתוני שעון מעודכנים`). Counting a day's full requirement against hours that have
not arrived would invent a deficit — the one thing this tool must never do.

Today is therefore never counted; its hours get their own line. Nor is a shift
still running past midnight: the night before stays unsettled until its session
closes, rather than settling with its hours missing and its whole requirement
due. An entry more than 16 hours old with no exit is taken for a forgotten
clock-out, not a shift, and the day settles and is named as open. Settling today
as soon as it holds a closed `דיווח` row would not do: a day that began with a row
of 06:00–06:45 would look finished by breakfast, draw its whole 9.00 requirement
and report a deficit for a day not yet worked. A finished row is not a finished
day.

Leave, sickness and reserve duty are credited against that day's requirement, so
time off is neutral. A row with hours on it credits those hours; one without
credits the day's requirement. The credit is capped at what remains of the
requirement, so half a day of leave beside half a day of work cannot credit a full
day. Turn it off with `--strict-attendance`.

A day type the tool has not met is counted as time off — Hilan's list of leave
types is long and keeps growing, while the ones that mean work are few — and
reported every time, because it is still a guess and should be heard as one.

The month's requirement (`MONTH … left of`) is the sum of every day's own
requirement, Hilan's where it states one, the rule's otherwise — the same figures
the balance uses, so the two can never disagree.

## When can I leave today

The `TODAY` block answers it. Today is never settled, so it stays out of
`BANKED` — letting it move the banked figure would be the same error this tool
exists to avoid. `NOW` includes it on purpose, and says it is conditional.

Leave taken today counts toward today's requirement, and the block shows it
(`4.00 leave`): half a day off leaves half a day to work, not a whole one.

Two times are offered. **Leave to close today** is measured from the clock-in plus
whatever is already recorded for the day, so the answer does not drift as the
hours pass: in at 08:30 with 0.50 already recorded earlier against a 9.00 day
gives 17:00, and it says 17:00 at nine in the morning and at four in the
afternoon. **Leave to end level** spends the surplus instead — if you are 5.00 up,
only 4.00 of today's 9.00 is needed, 0.50 of it is already done, so 12:00.

The entry is read from `דיווח` first and from the raw `דיווחי שעון` column second,
because the punch lands in the clock column before the reported row is built from
it — looking only at `דיווח` would mean asking to be told something the page is
already showing.

Until either column has it, a punch takes a couple of hours to reach Hilan and the
tool genuinely cannot see that you are in. It says so rather than guessing;
`--in 08:30` supplies the time and marks it `(assumed)`. A real punch, from either
column, always wins over a supplied one, and so does a finished row: a supplied
entry that a closed row already covers, or that comes before one ends, is ignored
rather than counted twice.

The exit works the same way. A reported row still open whose clock punch has
closed is over — the exit has reached the clock column and not yet the report —
so the session ends there instead of running on. So is a row with a total and
no exit: it is worth its total, and nothing runs from its entry.

## History and logs

Every run records its **settled** days to `history.json` in the config directory
— whatever it prints, `--json`, `--say` and `--brief` included:
the punches as written (`08:30-17:30`, an open one as `08:30-?`), hours worked
and credited, the requirement and the difference. Unsettled days are left out —
one is still moving, and a history that rewrites itself is not a history. So is
a run that pretends (`--today`, `--now`) or counts differently
(`--strict-attendance`): what it settles is not what Hilan holds.

A `history.json` that cannot be read as JSON is moved aside
(`history.json.unreadable-<date>`), not written over, and the run says where.

Hilan can restate a day long after the fact. A day that comes back different from
what was stored is reported rather than quietly overwritten:

```
  Hilan has changed days already recorded
    01/09  worked 7.00 -> 9.17, balance -2.00 -> 0.17
```

`hilan history` totals it by month. Since Hilan only ever shows one month, this
is what makes a question spanning months answerable without fetching them again.

Every run also writes to `hilan.log` there (rotated, 512 KB × 3):
the options as parsed, each request with its status and timing, the numbers
computed, and the whole traceback when something breaks.

```
2026-09-22 16:45:00 INFO    run: hilan
2026-09-22 16:45:03 INFO    GET .../calendarpage.aspx?isOnSelf=true -> 200 in 2.66s
2026-09-22 16:45:11 INFO    POST .../calendarpage.aspx?isOnSelf=true -> 200 in 8.30s  body: …&__VIEWSTATE=<redacted>&…
2026-09-22 16:45:12 INFO    parsed 30 day(s); settled through 2026-09-21; worked 106.25, balance 4.75
```

A log is the thing people paste into a chat when they ask for help, so the
password, the session cookies and `__VIEWSTATE` are stripped on the way in rather
than trusted not to turn up. Field names survive: `password=<redacted>` says more
than a missing line and says nothing secret. The login request is logged by
its field names alone. Every file the tool writes — log, history, cookies,
`.env` — is created readable by you only (`0600`, in a `0700` directory), never
opened wider first and narrowed after. A directory that cannot be written means
no log rather than no run.

## When the clocks move

Israel moves its clocks on the Friday before the last Sunday of March and back on
the last Sunday of October. A session spanning either one has a wall-clock length
an hour away from the time actually elapsed.

Nothing is corrected for it, deliberately. Hilan measures wall clock — a row's
own total equals `exit - entry` with no adjustment, including in the week of the
change — and Hilan's figure is what payroll pays. Matching it is the job; "correcting" the hour would put this tool a full
hour from the employer's own number and call that accuracy.

The spring change lands on a Friday, which is not a working day. The autumn one
falls on a Sunday, a working day, but at 02:00.

## Read-only

`דיווח ועדכון` is an editable form with a `שמירה` button. Every outgoing request —
and every hop of a redirect — passes a guard in `client.py`, which lets through
what is recognisably a read and raises on anything else. It refuses a request
that:

- goes anywhere other than the configured Hilan site, or by anything but https
  on the standard port;
- uses a method other than GET/HEAD/POST;
- carries postback or callback fields, or a save button as a name or a value, in
  its query string — ASP.NET reads fields from there too, and treats such a GET
  as a postback;
- carries a `%uXXXX` escape anywhere: ASP.NET decodes those, the checks here
  would not, and nothing this tool sends needs one;
- is a POST with any body but a form (`application/x-www-form-urlencoded`): a
  multipart or JSON body would carry fields the checks never read;
- is a POST to any path but the login endpoint and the attendance page, compared
  exactly, and as sent: an escaped character, `..` or `//` in it is refused
  rather than decoded;
- is a login carrying any field the login does not send (names compared exactly,
  since Hilan ignores their case), or one that asks to change the password;
- is an attendance POST carrying a field the month read never sends, naming a
  postback target, or without exactly one known read action;
- carries any of `btnSave`, `btnClear`, `btnPickStepProject`, `CollectiveAttendance`,
  in any case, in a name or a value of an attendance POST or of a query string;
- sends any field twice (ASP.NET joins repeats with commas, so two empty
  `newPassword` fields would read as one that is not empty).

A POST carrying `btnSave` is refused before it leaves the machine, and the tests
hold the guard to every rule above. The tool cannot save anything even if the
code around it is wrong. The widget's guard follows the same rules.

## Hilan API map

Taken from the live site, and written down here so nobody has to work it out again
after Hilan is redesigned.

### Login

The form's `user_nm` / `password_nm` fields have no `name` attribute — the form does
not submit them. `LoginManager.min.js` does the work, posting to
`Public/api/LoginApi` under `__rootPath = '/HilanCenter'`:

```
POST /HilanCenter/Public/api/LoginApi/LoginRequest
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest

orgId=<org id>&username=<number>&password=<plain text>&isChangePassword=false
  &newPassword=&id=&isEn=false
```

**`orgId` is required and is not implied by the subdomain.** The login page embeds
an `initialData` model carrying it:

```json
{"State":2,"OrgId":"1000","IsShowOrganizationSelection":false,"IsShowId":false, …}
```

Hilan answers an empty `orgId` with `הסיסמה או פרטי משתמש אינם נכונים` — the very
same message it uses for a wrong password. A correct password therefore looks
invalid, with nothing in the response to say otherwise, so the client reads the id
off the login page before posting. Where `IsShowId` is false, `id` may stay
empty; an organisation that sets it would also need the employee's ID number
(תעודת זהות), which this tool does not support yet.

The response is JSON with `IsFail`, `Code`, `ErrorMessage`, `IsShowVerificationCode`.
Codes: `1` bad credentials, `5` password change required, `6` expired, `8` bad
certificate. There is a second-factor branch (`ResendSms`, "remember this browser"),
which is why the session is kept in `cookies.json` in the config directory — logging
in rarely is what keeps the SMS challenge away. Hilan's load balancer sets the same
cookie name on several paths, so the jar is stored as a list, not a mapping.

A stale session is redirected to `/login`, and that page is ASP.NET too: it carries
a `__VIEWSTATE` of its own. Checking for one is therefore not enough to tell "here
is your month" from "log in again" — check that the response landed on the
attendance page and rendered `calendar_container`.

### Attendance data

`GET /Hilannetv2/Attendance/calendarpage.aspx?isOnSelf=true` returns a grid holding
**today only**. The whole month comes back from one POST to the same URL:

```
__EVENTTARGET=            __EVENTARGUMENT=          __LASTFOCUS=
Time=9                    DisableTimeout=true       H-XSRF-Token=
__VIEWSTATE=<from the GET>      __VIEWSTATEGENERATOR=<from the GET>
__calendarSelectedDays=9740,9741,…,9769
ctl00$mp$Strip$hCurrentItemId=<employee id>
ctl00$mp$currentMonth=01/09/2026
ctl00$mp$RefreshSelectedDays=ימים נבחרים
```

A page with ASP.NET event validation turned on also gets its
`__EVENTVALIDATION` back, as a browser would send it.

`__calendarSelectedDays` holds day serials counted from **2000-01-01**
(01/09/2026 = 9740). Another month needs the same single request with a different
`currentMonth` and that month's serials — no separate navigation postback.
`__VIEWSTATE` is 108 bytes (the state lives on the server) and a plain GET resets
the view to the current month, so nothing sticks between requests.

### Parsing the page

Values live in an `ov` ("original value") attribute on the cells. Days and segments
are addressed by `id` (`_row_N`, `_row_N_K`) rather than by nesting, so real nested
tables inside a cell cannot throw the parser off.

| What | Where |
|---|---|
| Grid | `#..._RG_Days_{emp}_{yyyy}_{mm}_reportsGrid_innerBody` |
| Date | `td[id*="cellOf_ReportDate_row_N"]` → `ov="01/09 יום ג"` |
| Clock | `cellOf_OriginalEntry/OriginalExit_ClockReports_row_N_K` |
| `דיווח` | `cellOf_ManualEntry/ManualExit/ManualTotal/StandardWorkHours/Comment_EmployeeReports_row_N_K` |
| Day type | `td[id*="cellOf_Symbol.SymbolId_..."] option[selected]` |
| Holiday or error | `td[id$="_special_row_N"]` |
| Error day | calendar cell with class `cED` |
| Absence day | calendar cell with class `calendarAbcenseDay` |
| Summary | `#..._DynamicLegendDiv` |
| Clock freshness | `#..._LastUpdateLegendText` |

Three traps that are easy to fall into:

1. **An empty cell arrives as the literal text `&nbsp;`** — Hilan escapes the
   entity twice.
2. **`special_row_N` doubles as the error slot.** `קיים דיווח ללא פרויקט באותה השורה`
   is not a holiday but a validation message, and one that clears itself once the
   punch finishes syncing. Days past `settled_through` therefore report such
   messages as waiting to sync, not as something to go and fix.
3. **The summary panel always shows the current payroll month**, not the one being
   viewed: open August and you still get September's `תקן`/`בפועל`. The comparison
   with Hilan is therefore made only for the current month and hidden otherwise.
   Quietly reporting another month's numbers would be the worst possible bug in a
   tool built to catch exactly that.

## Development

```bash
git clone https://github.com/benzine12/hilan-hours.git
cd hilan-hours
python3 -m venv .venv
# macOS / Linux: . .venv/bin/activate
# Windows:       py -m venv .venv, then .venv\Scripts\activate
python -m pip install -e ".[test]"
python -m pytest -q
```

GitHub Actions runs the same on macOS, Linux and Windows, with Python 3.11
through 3.14, and builds the package, installs the wheel on its own and runs it
from another directory (`.github/workflows/tests.yml`).

The core (`serial`, `standard`, `parser`, `calc`, `render`, `text`) knows nothing
about the network and is covered by tests built on two made-up months
(`tests/capture.py`): the markup is Hilan's own, taken from a live page, and every
name, number, time, absence and comment in it is invented. `tests/conftest.py`
renders it back into markup shaped the way Hilan serves it.

No test touches the network, the machine's password store or your own config
directory: every test runs against the made-up site
`https://example.net.hilan.co.il`, an in-memory password store and an empty
config directory of its own — and at one fixed moment, midday on the made-up
September's 23rd, so a test passes or fails the same on any day it is run.

The widget tests need `node` on the PATH and are skipped without it.

Never commit a page saved with `hilan fetch`: it is your real attendance, with
your name and employee number in it. `.gitignore` keeps `*.html` out for that
reason.

## License

MIT — see [LICENSE](LICENSE).
