"""HTTP access to Hilan — read paths only.

Hilan's attendance page is an editable form with a Save button, so this client
is built so that a write is not merely avoided but impossible: every outgoing
request passes a guard that rejects anything outside a small read allowlist.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from urllib.parse import parse_qs, unquote_plus

import httpx

from . import log as hlog
from .config import (
    ATTENDANCE_PATH,
    COOKIE_FILE,
    Credentials,
    LOGIN_PATH,
    base_url,
    ensure_private_dir,
    normalise_url,
    write_private,
)
from .serial import selected_days_field


class ReadOnlyViolation(RuntimeError):
    """Raised when a request would, or might, change data in Hilan."""


class LoginFailed(RuntimeError):
    pass


class Blocked(LoginFailed):
    """Hilan's application firewall refused the client outright.

    Not a password problem and not an expired session: neither retrying nor
    logging in again can help, so it is named separately.
    """


class VerificationRequired(LoginFailed):
    """Hilan wants an SMS/e-mail code for this client."""


class Rejected(LoginFailed):
    """Hilan turned the credentials down: wrong, expired, or to be changed.

    Named apart because sending them again cannot help and can lock the account.
    """


# Submit buttons on the attendance form that write, clear or bulk-edit data.
FORBIDDEN_FIELDS = (
    "btnSave",
    "btnClear",
    "btnPickStepProject",
    "CollectiveAttendance",
)

# The attendance page always renders this container; the login page never does,
# which is how we tell "session expired" from "here is your month".
ATTENDANCE_MARKER = "calendar_container"

# The organisation id lives in the login page's initialData model. Hilan
# rejects an empty one with the same message it uses for a wrong password,
# so guessing it wrong makes a correct password look invalid.
LOGIN_PAGE_PATH = "/login"
_ORG_ID_RE = re.compile(r'OrgId\\*"\s*:\s*\\*"(\d+)')

# The only POST actions this tool ever performs.
READ_ACTIONS = (
    "RefreshSelectedDays",
    "RefreshPeriod",
    "RefreshErrorsDays",
)

#: Every field the month read sends. An attendance POST carrying anything else
#: is not this tool's read, whatever else it looks like.
READ_FIELDS = frozenset({
    "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "Time", "DisableTimeout",
    "__VIEWSTATE", "__VIEWSTATEGENERATOR", "H-XSRF-Token", "__calendarSelectedDays",
    "ctl00$mp$Strip$hCurrentItemId", "ctl00$mp$currentMonth", "__EVENTVALIDATION",
    *(f"ctl00$mp${action}" for action in READ_ACTIONS),
})

#: Every field the login sends, with the verification code when Hilan asks for
#: one. Names are matched exactly: Hilan binds form fields regardless of case,
#: so an "IsChangePassword" must not slip past a check for "isChangePassword".
LOGIN_FIELDS = frozenset({
    "orgId", "username", "password", "isChangePassword", "newPassword", "id", "isEn",
    "verificationCode", "saveBrowser",
})

#: What makes an ASP.NET request a postback, wherever it is carried: a GET with
#: these in its query string is processed as one.
POSTBACK_FIELDS = ("__viewstate", "__eventtarget", "__eventargument", "__eventvalidation",
                   "__callbackid", "__callbackparam", "__previouspage", "__viewstatefieldcount",
                   "__viewstategenerator", "__lastfocus", "__scrollpositionx", "__scrollpositiony")

FORM = "application/x-www-form-urlencoded"


def _guard(request: httpx.Request, allowed_host: str) -> None:
    """Let through what is recognisably a read, and nothing else.

    It runs on every request, including each hop of a redirect, so a redirect
    cannot carry the password or a write anywhere the first request could not
    have gone.
    """
    url = request.url
    # A login carried in the address goes wherever the address does.
    if url.userinfo:
        raise ReadOnlyViolation("refusing an address that carries a login")
    if url.host != allowed_host:
        raise ReadOnlyViolation(f"refusing to contact foreign host {url.host!r}")
    if url.scheme != "https" or url.port not in (None, 443):
        raise ReadOnlyViolation(
            f"refusing {url.scheme} on port {url.port or 'default'}: only https is used"
        )

    method = request.method.upper()
    if method not in ("GET", "HEAD", "POST"):
        raise ReadOnlyViolation(f"{method} is never needed; refusing")

    # ASP.NET decodes %uXXXX escapes, which nothing here does: a name or value
    # written that way would pass every check below unread. None is ever sent.
    if b"%u" in url.raw_path.lower():
        raise ReadOnlyViolation("request carries a %u escape; refusing")

    # ASP.NET reads fields from the query string as well as the body, and treats
    # a GET carrying postback fields as a postback: the query is checked too.
    query = [key.lower() for key in url.params.keys()]
    for key, value in url.params.multi_items():
        key, value = key.lower(), value.lower()
        if key in POSTBACK_FIELDS or any(
            name.lower() in key or name.lower() in value for name in FORBIDDEN_FIELDS
        ):
            raise ReadOnlyViolation(f"query string carries {key!r}; refusing")
    if method in ("GET", "HEAD"):
        return

    # Only a form body is inspected below, so only a form body may be sent: a
    # multipart or JSON body would carry fields the checks never see.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != FORM:
        raise ReadOnlyViolation(f"POST with a {content_type or 'missing'} body; only {FORM} is sent")

    # The path as sent, before any decoding: an encoded dot or slash could make
    # the server resolve a path other than the one checked below.
    raw_path = url.raw_path.split(b"?", 1)[0]
    if b"%" in raw_path or b".." in raw_path or b"//" in raw_path:
        raise ReadOnlyViolation(f"POST to an unusual path {raw_path!r}; refusing")

    try:
        body = request.content.decode("utf-8", "replace")
    except httpx.RequestNotRead:
        # httpx resending a POST across a 307/308 redirect without its body.
        raise ReadOnlyViolation("a POST is never resent across a redirect") from None
    if "%u" in body.lower():
        raise ReadOnlyViolation("request carries a %u escape; refusing")
    fields = parse_qs(body, keep_blank_values=True)
    # ASP.NET joins a repeated field with commas: two empty newPassword fields
    # read as ",", which is not empty. Nothing here ever sends a field twice.
    repeated = sorted(name for name, values in fields.items() if len(values) > 1)
    if repeated:
        raise ReadOnlyViolation(f"a field is sent twice ({', '.join(repeated)}); refusing")
    path = url.path
    if path == LOGIN_PATH:
        if query:
            raise ReadOnlyViolation("a login carries nothing in its query string; refusing")
        unexpected = sorted(set(fields) - LOGIN_FIELDS)
        if unexpected:
            raise ReadOnlyViolation(
                f"login carries fields it never sends ({', '.join(unexpected)}); refusing"
            )
        # The same endpoint changes a password when asked to; that is a write.
        if fields.get("isChangePassword", ["false"]) != ["false"] or any(
            fields.get("newPassword", [""])
        ):
            raise ReadOnlyViolation("a login that changes the password is a write; refusing")
        return
    if path != ATTENDANCE_PATH:
        raise ReadOnlyViolation(f"POST to {path!r} is outside the read allowlist")
    if set(query) - {"isonself"}:
        raise ReadOnlyViolation("attendance POST carries an unexpected query string; refusing")

    decoded = unquote_plus(body).lower()
    for name in FORBIDDEN_FIELDS:
        if name.lower() in decoded:
            raise ReadOnlyViolation(
                f"request carries the mutating field {name!r}; refusing to send"
            )
    unexpected = sorted(set(fields) - READ_FIELDS)
    if unexpected:
        raise ReadOnlyViolation(
            f"attendance POST carries fields a read never sends ({', '.join(unexpected)});"
            " refusing to send"
        )
    if any(fields.get("__EVENTTARGET", [""])):
        raise ReadOnlyViolation("attendance POST names a postback target; refusing to send")
    if sum(f"ctl00$mp${action}" in fields for action in READ_ACTIONS) != 1:
        raise ReadOnlyViolation(
            "attendance POST without exactly one known read action; refusing to send"
        )


def _started(request: httpx.Request) -> None:
    # On the request, not in a table: a request that never gets an answer
    # leaves nothing behind.
    request.extensions["hilan_started"] = time.monotonic()


def _finished(response: httpx.Response) -> None:
    """Log the exchange. The body is redacted by the log, not trusted to be safe."""
    request = response.request
    elapsed = time.monotonic() - request.extensions.get("hilan_started", time.monotonic())
    body = None
    if request.method == "POST":
        text = request.content.decode("utf-8", "replace")
        if request.url.path == LOGIN_PATH:
            # Field names only: who is logging in, with what, is nobody's
            # business in a log people paste into chats.
            text = "&".join(f"{name}=…" for name in parse_qs(text, keep_blank_values=True))
        # Redacted before it is cut short, so a cut can never split a secret
        # field from the marker that hides it.
        body = hlog.redact(text)[:400]
    hlog.request_done(
        request.method, str(request.url), response.status_code, elapsed,
        int(response.headers.get("content-length") or 0) or None, body=body,
    )


def build_client(transport: httpx.BaseTransport | None = None,
                 base: str | None = None) -> httpx.Client:
    """A client bound to one Hilan site, with the guard on every request.

    The address is read here rather than at import, so a missing one is an
    error about configuration at the moment a request is due — not a crash
    in anything that merely imports this module.
    """
    base = normalise_url(base) if base else base_url()
    host = httpx.URL(base).host

    def guard(request: httpx.Request) -> None:
        _guard(request, host)

    return httpx.Client(
        base_url=base,
        # Hilan's month POST takes around eight seconds, and longer under
        # load, so the timeout is generous.
        timeout=90.0,
        follow_redirects=True,
        event_hooks={"request": [guard, _started], "response": [_finished]},
        transport=transport,
        headers={
            # An ordinary browser's: a public tool's own name in Hilan's logs
            # would tie it to the employee number of everyone who runs it.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "he-IL,he;q=0.9",
        },
    )


class HilanClient:
    """Logs in, then fetches whole months of attendance HTML."""

    def __init__(self, transport: httpx.BaseTransport | None = None,
                 base: str | None = None) -> None:
        self._http = build_client(transport=transport, base=base)
        self._load_cookies()

    def __enter__(self) -> "HilanClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._save_cookies()
        self._http.close()

    # -- session persistence: logging in rarely avoids SMS challenges --------

    def _load_cookies(self) -> None:
        """Reuse the stored session; anything unreadable is simply no session."""
        try:
            stored = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):            # missing, garbled, not UTF-8
            return
        if not isinstance(stored, list):
            return
        now = time.time()
        for item in stored:
            try:
                expires = item.get("expires")
                if isinstance(expires, (int, float)) and expires <= now:
                    continue                     # Hilan has already let it go
                self._http.cookies.set(
                    item["name"], item["value"],
                    domain=item.get("domain") or self._http.base_url.host,
                    path=item.get("path") or "/",
                )
                if isinstance(expires, (int, float)):
                    domain = item.get("domain") or self._http.base_url.host
                    for c in self._http.cookies.jar:
                        if (c.name, c.path, c.domain) == (item["name"], item.get("path") or "/", domain):
                            c.expires = int(expires)
            except (KeyError, TypeError, AttributeError):
                continue

    def _save_cookies(self) -> None:
        """Store cookies with their domain and path.

        Hilan's load balancer sets the same cookie name on several paths, and
        flattening the jar into a name->value mapping raises CookieConflict the
        moment that happens.
        """
        try:
            ensure_private_dir(COOKIE_FILE.parent)
            jar = [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                 "expires": c.expires}
                for c in self._http.cookies.jar if not c.is_expired()
            ]
            write_private(COOKIE_FILE, json.dumps(jar, indent=2))
        except OSError:
            pass

    # -- auth ---------------------------------------------------------------

    def is_authenticated(self) -> bool:
        try:
            r = self._http.get(ATTENDANCE_PATH, params={"isOnSelf": "true"})
        except httpx.HTTPError:
            return False
        return ATTENDANCE_MARKER in r.text

    def org_id(self) -> str:
        """Read the organisation id the login page declares for this site.

        A page that does not declare one gets an empty id — better a try than
        refusing to log in at all. A page that cannot be fetched is an error,
        not an empty id: logging in blind would only cost a failed attempt.
        """
        page = self._http.get(LOGIN_PAGE_PATH)
        if page.status_code == 403:
            raise Blocked(
                "Hilan refused the request (403) — its firewall is turning"
                " this client away, usually after too many requests. Wait a"
                " while and try again."
            )
        page.raise_for_status()
        found = _ORG_ID_RE.search(page.text)
        return found.group(1) if found else ""

    def login(self, creds: Credentials, verification_code: str | None = None) -> None:
        # Mirror what LoginManager.min.js sends: every field of its model, as an
        # XHR, with the organisation id taken from the page it was served on.
        payload = {
            "orgId": self.org_id(),
            "username": creds.username,
            "password": creds.password,
            "isChangePassword": "false",
            "newPassword": "",
            "id": "",
            "isEn": "false",
        }
        if verification_code:
            payload["verificationCode"] = verification_code
            payload["saveBrowser"] = "true"
        r = self._http.post(
            LOGIN_PATH, data=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if r.status_code == 403:
            raise Blocked(
                "Hilan refused the request (403) — its firewall is turning this"
                " client away, usually after too many requests. Wait a while"
                " and try again."
            )
        # Unauthorised, from the endpoint that judges the password, is a no to it.
        if r.status_code == 401:
            raise Rejected("Hilan refused the login (401)")
        r.raise_for_status()
        try:
            result = r.json()
        except json.JSONDecodeError as exc:
            raise LoginFailed("login endpoint did not return JSON") from exc

        # Accepted only when Hilan says so. An answer of any other shape is not
        # taken for success: storing a password on the strength of it, or
        # sending it again on every run, is how an account gets locked.
        if not isinstance(result, dict):
            result = {}
        if result.get("IsFail") is False:
            return
        if result.get("IsFail") is not True:
            raise Rejected(
                "Hilan answered the login in a form this tool does not recognise;"
                " not sending the password again until 'hilan login'"
            )
        def said(field: str, fallback: str) -> str:
            # Hilan's own words, minus anything secret it might echo back, and
            # short: they go to the screen and the log.
            text = str(result.get(field) or fallback)
            for secret in (creds.password, verification_code):
                if secret:
                    text = text.replace(secret, "<redacted>")
            return hlog.redact(text)[:200]

        if result.get("IsShowVerificationCode") or result.get("IsShowVerification"):
            raise VerificationRequired(
                said("VerificationCodeSentText", "Hilan requires a verification code for this client"))
        raise Rejected(said("ErrorMessage", f"login failed (code {result.get('Code')})"))

    # -- data ---------------------------------------------------------------

    def fetch_month(self, year: int, month: int) -> str:
        """HTML of the attendance grid with every day of the month expanded.

        The page defaults to showing today only; the whole month comes back by
        submitting every day's calendar serial in __calendarSelectedDays.
        """
        page = self._http.get(ATTENDANCE_PATH, params={"isOnSelf": "true"})
        # "Not you" is not "not working": Hilan refuses the page outright once
        # the session is revoked, and logging in again is the whole fix.
        if page.status_code in (401, 403):
            raise LoginFailed(
                f"session refused ({page.status_code}) — logging in again"
            )
        page.raise_for_status()
        # A stale session is redirected to /login, which is an ASP.NET page and
        # carries a __VIEWSTATE of its own — so the hidden fields alone cannot
        # tell the two apart. Check that we actually landed on the grid.
        if page.url.path != ATTENDANCE_PATH or ATTENDANCE_MARKER not in page.text:
            raise LoginFailed(
                "session expired — run 'hilan login' (Hilan redirected to "
                f"{page.url.path})"
            )
        form = _hidden_fields(page.text)
        if "__VIEWSTATE" not in form:
            raise LoginFailed("attendance page rendered without __VIEWSTATE")

        body = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "Time": form.get("Time", "9"),
            "DisableTimeout": "true",
            "__VIEWSTATE": form["__VIEWSTATE"],
            "__VIEWSTATEGENERATOR": form.get("__VIEWSTATEGENERATOR", ""),
            "H-XSRF-Token": form.get("H-XSRF-Token", ""),
            "__calendarSelectedDays": selected_days_field(year, month),
            "ctl00$mp$Strip$hCurrentItemId": form.get("ctl00$mp$Strip$hCurrentItemId", ""),
            "ctl00$mp$currentMonth": f"01/{month:02d}/{year}",
            "ctl00$mp$RefreshSelectedDays": "ימים נבחרים",
        }
        # A page with ASP.NET event validation turned on refuses a postback
        # that does not hand its token back.
        if form.get("__EVENTVALIDATION"):
            body["__EVENTVALIDATION"] = form["__EVENTVALIDATION"]
        r = self._http.post(ATTENDANCE_PATH, params={"isOnSelf": "true"}, data=body)
        if r.status_code in (401, 403):
            raise LoginFailed(f"session refused ({r.status_code}) — logging in again")
        r.raise_for_status()
        # The session can lapse between the GET and the POST; then the answer is
        # the login page again, and a parser error about a missing grid would
        # hide what is simply "log in again".
        if r.url.path != ATTENDANCE_PATH or ATTENDANCE_MARKER not in r.text:
            raise LoginFailed(
                f"the month did not come back (Hilan answered with {r.url.path})"
            )
        return r.text


def _hidden_fields(html: str) -> dict[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    return {
        i["name"]: i.get("value", "")
        for i in soup.select("input[type=hidden][name]")
    }


#: Inputs whose value is a session secret, by name in any case: the view state
#: (also split as __VIEWSTATE1..N), event validation, tokens, passwords, codes.
_SECRET_NAME = re.compile(
    r"^__viewstate\d*$|^__viewstate|^__eventvalidation$|xsrf|token|password|verification",
    re.IGNORECASE,
)


def redact(html: str) -> str:
    """Strip session secrets before a saved page touches the disk.

    Done with the same parser the page is read with, so whatever form an input
    takes — attribute order, quoting, entities, repeated attributes — what is
    blanked is exactly what would be read.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["input", "textarea", "meta"]):
        name = tag.get("name") or tag.get("id") or ""
        if isinstance(name, list):
            name = " ".join(name)
        if not _SECRET_NAME.search(name):
            continue
        if tag.name == "input":
            tag["value"] = "REDACTED"
        elif tag.name == "textarea":
            tag.string = "REDACTED"
        else:
            tag["content"] = "REDACTED"
    return str(soup)
