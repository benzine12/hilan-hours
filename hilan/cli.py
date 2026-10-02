# -*- coding: utf-8 -*-
"""Command line entry point."""

from __future__ import annotations

import functools
import getpass
import json as jsonlib
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import click
import httpx
from rich import box
from rich.console import Console
from rich.table import Table

from . import clock, config, history
from . import log as hlog
from .calc import analyse, hrs
from .client import (
    HilanClient,
    LoginFailed,
    ReadOnlyViolation,
    Rejected,
    VerificationRequired,
    redact,
)
from .config import CredentialsUnavailable, NotConfigured
from .parser import parse_month
from .render import render_brief, render_days, render_summary, to_dict
from .say import spoken


def _parse_month_arg(value: str) -> tuple[int, int]:
    try:
        year, month = value.split("-")
        y, m = int(year), int(month)
        date(y, m, 1)
        return y, m
    except (ValueError, TypeError) as exc:
        raise click.BadParameter(f"expected YYYY-MM, got {value!r}") from exc


def _fetch_months(client: HilanClient, months) -> list[str]:
    """Fetch each month, renewing the session once if it lapses mid-flight.

    is_authenticated() passing is not a promise that the next request will be
    served — Hilan can drop the session in between, and the fetch then lands on
    /login. Failing there would ask for a command the tool can run itself, with
    a password it already holds. Months already fetched are kept, not fetched
    again; a second lapse in one run is a real problem and is raised.
    """
    pages, renewed = [], False
    for year, month in months:
        try:
            pages.append(client.fetch_month(year, month))
        except Rejected:
            raise
        except LoginFailed:
            if renewed:
                raise
            _authenticate(client, force=True)
            renewed = True
            pages.append(client.fetch_month(year, month))
    return pages


def _authenticate(client: HilanClient, force: bool = False) -> None:
    """Reuse the stored session; log in only once it has expired.

    A password Hilan has refused is not tried again on its own: a scheduled
    run would send it every time, and that is how an account gets locked.
    """
    if not force and client.is_authenticated():
        return
    refused = config.refused_since()
    if refused:
        raise CredentialsUnavailable(
            f"Hilan refused the stored password or asked for a code ({refused}) — run"
            " `hilan login` in a terminal. Until then no login is tried, so as not to"
            " lock the account or send SMS after SMS."
        )
    creds = config.resolve_credentials()
    try:
        try:
            client.login(creds)
        except VerificationRequired as exc:
            # A scheduled run has nobody to type the code: each run would only
            # send another SMS. Stop until someone runs `hilan login`. The same
            # if the code typed here is not accepted, or not typed at all.
            config.mark_refused("verification code wanted")
            if not sys.stdin.isatty():
                raise CredentialsUnavailable(
                    "Hilan wants a verification code — run `hilan login` in a terminal"
                ) from exc
            click.echo(f"Hilan wants a verification code: {exc}", err=True)
            code = click.prompt("Code", type=str)
            client.login(creds, verification_code=code)
            config.clear_refused()
    except Rejected as exc:
        config.mark_refused()
        raise Rejected(
            f"Hilan refused the stored password: {exc} — run `hilan login` to enter it again"
        ) from exc


def _utf8_output() -> None:
    """Make Hebrew survive a Windows pipe.

    Python writes to a Windows console in UTF-16 already, but to a pipe or a
    file in the ANSI code page, which has no Hebrew and raises on the first
    letter of it — so `hilan --json > month.json` would fail there.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8":
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError, OSError):
                pass


def _friendly(what: str):
    """Turn the failures a person can act on into a message, not a traceback.

    The traceback still goes to the log, where it can be investigated.
    """
    def wrap(command):
        @functools.wraps(command)
        def run(*args, **kwargs):
            try:
                return command(*args, **kwargs)
            except click.ClickException:
                raise
            except (LoginFailed, CredentialsUnavailable, NotConfigured) as exc:
                hlog.failure(what)
                raise click.ClickException(str(exc)) from exc
            except ReadOnlyViolation as exc:
                hlog.failure(what)
                raise click.ClickException(f"refused to send a request: {exc}") from exc
            except httpx.HTTPError as exc:
                # Hilan takes seconds to answer and sometimes longer than that.
                # A stack through httpx internals tells the person at the
                # terminal nothing they can act on; it belongs in the log.
                hlog.failure(what)
                raise click.ClickException(
                    f"Hilan did not answer properly ({type(exc).__name__}) — try again in a moment"
                ) from exc
            except UnicodeDecodeError as exc:
                hlog.failure(what)
                raise click.ClickException(
                    "a saved page is not UTF-8 — save it again from the browser"
                ) from exc
            except UnicodeEncodeError as exc:  # noqa: F841
                # Its message names the character and where it sits: in a
                # password, that is part of the password. Say what, not which.
                hlog.logger().error("failed: %s (a character that cannot be sent)", what)
                raise click.ClickException(
                    "the employee number or password holds a character that cannot be sent"
                ) from None
            except (UnicodeError, httpx.StreamError) as exc:
                # A malformed address (a broken xn-- label) or a request httpx
                # will not send: nothing left, so say so without a traceback.
                hlog.failure(what)
                raise click.ClickException(
                    f"Hilan's address or answer could not be handled ({type(exc).__name__})"
                ) from exc
            except OSError as exc:
                hlog.failure(what)
                raise click.ClickException(
                    f"could not use {exc.filename or 'a file'}: {exc.strerror or exc}"
                ) from exc
        return run
    return wrap


def _open_client(base: str | None = None) -> HilanClient:
    """A client for the configured site, or a plain message saying there is none."""
    try:
        return HilanClient(base=base) if base else HilanClient()
    except NotConfigured as exc:
        raise click.ClickException(str(exc)) from exc


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(f"--today wants a date as YYYY-MM-DD, got {value!r}") from exc


def _parse_clock(value, flag):
    """A wall-clock HH:MM, or None. Anything else is a mistake worth naming."""
    if not value:
        return None
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError) as exc:
        raise click.BadParameter(f"{flag} wants a time as HH:MM, got {value!r}") from exc


def _months_needed(target: tuple[int, int], today: date) -> list[tuple[int, int]]:
    """The target month, plus the previous one when this week reaches back into it."""
    week_start = today - timedelta(days=(today.weekday() + 1) % 7)
    spill = (week_start.year, week_start.month)
    viewing_current_month = target == (today.year, today.month)
    if viewing_current_month and spill != target:
        return [spill, target]
    return [target]


def _read_page(path: Path):
    """A saved page, parsed; a page that is not an attendance page says so."""
    try:
        return parse_month(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise
    except ValueError as exc:
        raise click.ClickException(f"{path} is not a Hilan attendance page: {exc}") from exc


def _record_history(analysis, err_console: Console):
    """Keep the settled days; a history that cannot be written must not stop the run."""
    try:
        amendments = history.record(analysis)
    except OSError as exc:
        hlog.failure("recording history")
        err_console.print(f"  history not recorded: {exc.strerror or exc}", style="yellow")
        return []
    if history.last_set_aside is not None:
        err_console.print(
            f"  history.json was unreadable; it was kept as {history.last_set_aside.name}",
            style="yellow",
        )
        history.last_set_aside = None
    return amendments


@click.group(invoke_without_command=True)
@click.option("--brief", "show_brief", is_flag=True,
              help="Just two numbers: hours done and hours banked.")
@click.option("--days", "show_days", is_flag=True, help="Per-day table.")
@click.option("--week", "show_week", is_flag=True,
              help="Per-day table for this week only.")
@click.option("--month", "month_arg", metavar="YYYY-MM", help="Month to show (default: the current one).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option("--from-file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              multiple=True, help="Read saved HTML instead of fetching; may be repeated.")
@click.option("--strict-attendance", is_flag=True,
              help="Do not credit leave or sick days against the requirement.")
@click.option("--today", "today_arg", metavar="YYYY-MM-DD",
              help="Treat this date as today (for checking).")
@click.option("--in", "entry_arg", metavar="HH:MM",
              help="Clock-in time Hilan has not synced yet, for the leave forecast.")
@click.option("--now", "now_arg", metavar="HH:MM",
              help="Treat this as the current time (for checking).")
@click.option("--say", "as_speech", is_flag=True,
              help="One sentence, for Siri or a notification.")
@click.option("--verbose", is_flag=True, help="Also print the log to the terminal.")
@click.pass_context
@_friendly("running hilan")
def main(ctx, show_brief, show_days, show_week, month_arg, as_json, from_file,
         strict_attendance, today_arg, entry_arg, now_arg, as_speech, verbose):
    """An honest count of your hours on top of Hilan."""
    _utf8_output()
    hlog.setup(argv=["hilan", *_describe(ctx)], verbose=verbose)
    if ctx.invoked_subcommand is not None:
        return

    today = _parse_date(today_arg) if today_arg else clock.today()
    assumed_entry = _parse_clock(entry_arg, "--in")
    now = datetime.combine(today, _parse_clock(now_arg, "--now") or clock.now().time())

    target = _parse_month_arg(month_arg) if month_arg else (today.year, today.month)

    if from_file:
        reports = [_read_page(p) for p in from_file]
    else:
        with _open_client() as client:
            # No session check first: fetch_month has to load that page
            # anyway and a lapsed session is retried, so asking beforehand
            # would cost a second round trip against a server that takes
            # seconds to answer.
            reports = [
                parse_month(page)
                for page in _fetch_months(client, _months_needed(target, today))
            ]

    try:
        analysis = analyse(
            reports, today=today, now=now, assumed_entry=assumed_entry,
            credit_absences=not strict_attendance,
            month=target if month_arg else None,
        )
    except ValueError as exc:
        if "no page for" in str(exc):
            raise click.ClickException(str(exc)) from None
        hlog.failure("computing the analysis")
        raise
    except Exception:
        hlog.failure("computing the analysis")
        raise
    hlog.logger().info(
        "parsed %d day(s); settled through %s; worked %s, balance %s",
        len(analysis.days), analysis.settled_through,
        hrs(analysis.now.worked), hrs(analysis.now.balance),
    )

    console, err_console = Console(), Console(stderr=True)
    # Hilan shows one month at a time, so anything spanning months means
    # fetching them again unless what has been seen is kept — whatever the
    # output asked for. A run given a pretend date settles at a date that never
    # happened, and one that drops the leave credit computes different days;
    # recording either would leave the history disagreeing with itself.
    pretending = bool(today_arg or now_arg or strict_attendance)
    amendments = [] if pretending else _record_history(analysis, err_console)

    if as_json:
        payload = to_dict(analysis)
        payload["amendments"] = [
            {"date": a.day.isoformat(), "was": a.was, "now": a.now} for a in amendments
        ]
        click.echo(jsonlib.dumps(payload, ensure_ascii=False, indent=2))
        return
    if as_speech:
        click.echo(spoken(analysis))
        # The sentence is what a Shortcut reads; a restated day still has to be
        # heard about, so it goes where a person running this would see it.
        _report_amendments(amendments, err_console)
        return
    if show_brief:
        render_brief(analysis, console)
    else:
        render_summary(analysis, console)
    _report_amendments(amendments, console)
    if show_week:
        render_days(analysis, console, only_week=True)
    if show_days:
        render_days(analysis, console)


def _username(given: str | None, from_stdin: bool) -> str:
    """The employee number: given, configured, or — only when it can be — asked for."""
    found = (
        (given or "").strip()
        or os.environ.get("HILAN_USER", "").strip()
        or config.read_env().get("HILAN_USER", "").strip()
        or config.load_username()
    )
    if found:
        return found
    if from_stdin:
        # Standard input carries the password; reading the number from it too
        # would send the password as the number, and write it into the log.
        raise click.ClickException(
            "--stdin reads only the password: give the employee number with"
            " --user or HILAN_USER"
        )
    try:
        return input("Employee number: ").strip()
    except EOFError:
        raise click.ClickException("no employee number given — use --user") from None


@main.command()
@click.option("--user", "user_arg", metavar="NUMBER", help="Your employee number.")
@click.option("--stdin", "from_stdin", is_flag=True,
              help="Read the password from standard input instead of asking.")
@_friendly("logging in")
def login(user_arg: str | None, from_stdin: bool) -> None:
    """Store your Hilan address and credentials."""
    asked_url = None
    try:
        url = config.base_url()
    except NotConfigured as exc:
        # With --stdin the input is the password, so there is nobody to ask.
        if from_stdin or config.load_url():
            raise click.ClickException(str(exc)) from exc
        try:
            typed = input(f"Hilan address (like {config.URL_EXAMPLE}): ")
        except EOFError:
            raise click.ClickException(str(exc)) from None
        try:
            url = asked_url = config.normalise_url(typed)
        except NotConfigured as bad:
            raise click.ClickException(str(bad)) from bad
    username = _username(user_arg, from_stdin)
    # A script can supply the password without a prompt: on standard input
    # with --stdin, or in HILAN_PASSWORD. Either line ending is stripped.
    if from_stdin:
        password = _stdin_line()
    elif os.environ.get("HILAN_PASSWORD"):
        password = os.environ["HILAN_PASSWORD"]
    else:
        password = getpass.getpass(f"Hilan password for {username}: ")
    if not password:
        raise click.ClickException("no password given — nothing was sent")
    creds = config.Credentials(username=username, password=password)

    with _open_client(url) as client:
        try:
            try:
                client.login(creds)
            except VerificationRequired as exc:
                click.echo(f"Hilan wants a verification code: {exc}", err=True)
                code = click.prompt("Code", type=str)
                client.login(creds, verification_code=code)
        except Rejected as exc:
            # Hilan counts this attempt too: until a login succeeds, nothing
            # sends a password on its own — the old one included.
            config.mark_refused()
            raise click.ClickException(f"login failed: {exc}") from exc
        except LoginFailed as exc:
            raise click.ClickException(f"login failed: {exc}") from exc

    config.save_username(username)
    if asked_url:
        config.save_url(asked_url)
    try:
        where = config.password_set(username, password)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"logged in, but saving the password failed: {exc}")
    # Only now: lifted before the new password is stored, a failure in between
    # would leave the old one to be sent by the next run.
    config.clear_refused()
    click.echo(f"Logged in as {username}; password saved to {where}.")
    if where == str(config._env_path()):
        why = ("There is no password store on this machine" if config._keyring() is None
               else "The password store would not take it")
        click.echo(f"  {why}, so that is a plain-text file readable only by you.", err=True)


@main.command()
@click.option("--month", "months", multiple=True, required=True, help="YYYY-MM; may be repeated.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), required=True,
              help="Folder to save the pages in.")
@click.option("--raw", is_flag=True, help="Keep __VIEWSTATE and tokens in the saved HTML.")
@_friendly("fetching pages")
def fetch(months: tuple[str, ...], out: Path, raw: bool) -> None:
    """Download a whole month of HTML (read-only)."""
    # Every month is checked before Hilan is asked for any of them.
    wanted = [_parse_month_arg(value) for value in months]
    out.mkdir(parents=True, exist_ok=True)
    with _open_client() as client:
        pages = _fetch_months(client, wanted)
    for (year, month), html in zip(wanted, pages):
        if not raw:
            html = redact(html)
        target = out / f"{year:04d}-{month:02d}.html"
        # A saved page is your attendance, with your name and number in it.
        config.write_private(target, html)
        click.echo(f"{target}  ({len(html):,} bytes)")


def _stdin_line() -> str:
    """One line of standard input, read as UTF-8.

    Windows decodes standard input in the console's ANSI code page, so a
    password with a letter outside it, piped in as UTF-8, would arrive garbled.
    """
    raw = getattr(sys.stdin, "buffer", None)
    line = raw.readline().decode("utf-8-sig") if raw is not None else sys.stdin.readline()
    return line.rstrip("\r\n").removeprefix("\ufeff")


def _describe(ctx) -> list[str]:
    """The options as given, rebuilt from what click parsed, and the subcommand.

    Reading sys.argv would be simpler and wrong: under a test runner, and under
    anything else that invokes the command in-process, argv belongs to the host.
    Flags are named as they are typed (--today), not as the code calls them.
    """
    flags = {
        p.name: max((o for o in p.opts if o.startswith("--")), key=len, default=p.opts[0])
        for p in ctx.command.params if getattr(p, "opts", None)
    }
    out = []
    for name, value in sorted(ctx.params.items()):
        if value in (None, False, "", ()):
            continue
        flag = flags.get(name, "--" + name.replace("_", "-"))
        if isinstance(value, tuple):
            for item in value:
                out += [flag, str(item)]
        elif value is True:
            out.append(flag)
        else:
            out += [flag, str(value)]
    if ctx.invoked_subcommand:
        out.append(ctx.invoked_subcommand)
    return out


def _report_amendments(amendments, console) -> None:
    """Hilan restating a settled day is news, not something to swallow."""
    if not amendments:
        return
    console.print()
    console.print("  Hilan has changed days already recorded", style="bold yellow")
    for a in amendments:
        def shown(value):
            return " ".join(value) if isinstance(value, list) else value

        changed = ", ".join(
            f"{f} {shown(a.was.get(f))} -> {shown(a.now[f])}"
            for f in history.FIELDS if a.was.get(f) != a.now[f]
        )
        console.print(f"    {a.day.strftime('%d/%m')}  {changed}", style="yellow")


@main.command("history")
@_friendly("reading history")
def history_cmd() -> None:
    """Totals per month from every run so far."""
    console = Console()
    rows = history.by_month()
    if not rows:
        click.echo("nothing recorded yet — run `hilan` first")
        return
    table = Table(box=box.SQUARE, show_lines=True, header_style="bold", padding=(0, 1))
    table.add_column("month")
    for col in ("days", "worked", "credited", "required", "balance"):
        table.add_column(col, justify="right")
    for r in rows:
        table.add_row(r.label, str(r.days), str(r.worked), str(r.credited),
                      str(r.required), _signed_decimal(r.balance))
    whole = history.total()
    table.add_row("total", str(whole.days), str(whole.worked), str(whole.credited),
                  str(whole.required), _signed_decimal(whole.balance), style="bold")
    console.print()
    console.print(table)
    console.print()


def _signed_decimal(value) -> str:
    return f"+{value}" if value > 0 else str(value)


if __name__ == "__main__":
    sys.exit(main())
