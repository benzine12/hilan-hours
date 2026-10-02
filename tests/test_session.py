# -*- coding: utf-8 -*-
"""Session persistence — logging in rarely is what keeps SMS challenges away."""
import json

import httpx
import pytest

from conftest import posix_modes
from hilan import config
from hilan.client import HilanClient

#: What the month POST answers with: the attendance page, grid and all.
MONTH_PAGE = '<html><div id="calendar_container"></div>the month</html>'


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(config, "COOKIE_FILE", path)
    monkeypatch.setattr("hilan.client.COOKIE_FILE", path)
    return path


def client(transport=None):
    return HilanClient(
        transport=transport
        or httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
    )


class TestRoundTrip:
    def test_cookies_survive_a_restart(self, cookie_file):
        c = client()
        c._http.cookies.set("session", "abc", domain="example.net.hilan.co.il", path="/")
        c.close()
        assert json.loads(cookie_file.read_text())[0]["name"] == "session"
        assert client()._http.cookies.get("session") == "abc"

    def test_same_name_on_two_paths_does_not_explode(self, cookie_file):
        # Hilan's load balancer really does this.
        c = client()
        for path in ("/", "/Hilannetv2/"):
            c._http.cookies.set("TS01abcdef", f"v{path}", domain="example.net.hilan.co.il",
                                path=path)
        c.close()
        stored = json.loads(cookie_file.read_text())
        assert sorted(i["path"] for i in stored) == ["/", "/Hilannetv2/"]

    @posix_modes
    def test_file_is_owner_only(self, cookie_file):
        c = client()
        c._http.cookies.set("session", "abc", domain="example.net.hilan.co.il", path="/")
        c.close()
        assert oct(cookie_file.stat().st_mode)[-3:] == "600"


class TestTolerance:
    def test_missing_file_is_fine(self, cookie_file):
        assert client()._http.cookies.get("session") is None

    def test_corrupt_file_is_ignored(self, cookie_file):
        cookie_file.write_text("{not json")
        assert len(client()._http.cookies.jar) == 0

    def test_unexpected_shape_is_skipped(self, cookie_file):
        cookie_file.write_text(json.dumps([{"nope": 1}, "garbage",
                                           {"name": "sid", "value": "s", "domain": "example.net.hilan.co.il"}]))
        jar = client()._http.cookies.jar
        assert [c.name for c in jar] == ["sid"]


class TestAuthCheck:
    def test_network_failure_reads_as_not_authenticated(self, cookie_file):
        def boom(request):
            raise httpx.ConnectError("no route", request=request)

        assert client(httpx.MockTransport(boom)).is_authenticated() is False

    def test_login_page_reads_as_not_authenticated(self, cookie_file):
        t = httpx.MockTransport(lambda r: httpx.Response(200, text="<form>login</form>"))
        assert client(t).is_authenticated() is False

    def test_attendance_page_reads_as_authenticated(self, cookie_file):
        t = httpx.MockTransport(
            lambda r: httpx.Response(200, text='<table id="calendar_container">')
        )
        assert client(t).is_authenticated() is True


class TestExpiredSessionIsNoticed:
    """A stale session redirects to /login, and that page is ASP.NET too.

    Checking merely that some __VIEWSTATE came back is not enough: the login
    page carries one: the redirect chain ends at /login with __VIEWSTATE
    present and no calendar_container. Without a landing check the
    client posts the login page's state to the attendance path and returns
    whatever comes back, so the failure surfaces later as a parser error about
    a missing grid instead of "log in again".
    """

    LOGIN_PAGE = (
        '<html><body><form>'
        '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" />'
        '<input type="hidden" name="__VIEWSTATEGENERATOR" value="0000ABCD" />'
        '<div id="loginBox">כניסה</div></form></body></html>'
    )

    def _redirecting_transport(self):
        def handler(request):
            if request.url.path == "/Hilannetv2/Attendance/calendarpage.aspx":
                return httpx.Response(
                    302, headers={"location": "/login?isOnSelf=true"}
                )
            return httpx.Response(200, text=self.LOGIN_PAGE)

        return httpx.MockTransport(handler)

    def test_fetch_month_refuses_the_login_page(self, cookie_file):
        from hilan.client import LoginFailed

        with client(self._redirecting_transport()) as c:
            with pytest.raises(LoginFailed, match="session"):
                c.fetch_month(2026, 9)

    def test_fetch_month_does_not_post_the_login_pages_state(self, cookie_file):
        """The refusal has to come before the POST, not after."""
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request)
                return httpx.Response(200, text="whatever")
            if request.url.path == "/Hilannetv2/Attendance/calendarpage.aspx":
                return httpx.Response(302, headers={"location": "/login?isOnSelf=true"})
            return httpx.Response(200, text=self.LOGIN_PAGE)

        from hilan.client import LoginFailed

        with client(httpx.MockTransport(handler)) as c:
            with pytest.raises(LoginFailed):
                c.fetch_month(2026, 9)
        assert posts == []

    def test_fetch_month_works_when_the_page_really_renders(self, cookie_file):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, text=MONTH_PAGE)
            return httpx.Response(
                200,
                text='<html><div id="calendar_container"></div><form>'
                '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" />'
                "</form></html>",
            )

        with client(httpx.MockTransport(handler)) as c:
            assert c.fetch_month(2026, 9) == MONTH_PAGE


class TestARefusedPageIsAnExpiredSession:
    """403 means "not you", not "not working", and the difference matters.

    The retry only looks for LoginFailed, so a 403 raised as a plain status
    error would escape it and end the run with "Hilan did not answer" — when
    logging in again is all it needs, with a password already in hand.
    """

    def _serving(self, status):
        def handler(request):
            if request.url.path == "/Hilannetv2/Attendance/calendarpage.aspx":
                return httpx.Response(status, text="nope")
            return httpx.Response(200, text="<html></html>")

        return httpx.MockTransport(handler)

    @pytest.mark.parametrize("status", [401, 403])
    def test_it_is_raised_as_a_login_problem(self, cookie_file, status):
        from hilan.client import LoginFailed

        with client(self._serving(status)) as c:
            with pytest.raises(LoginFailed, match="session"):
                c.fetch_month(2026, 9)

    def test_a_server_fault_is_not_mistaken_for_one(self, cookie_file):
        """A 500 is Hilan's problem and logging in again will not fix it."""
        import httpx as _httpx

        with client(self._serving(500)) as c:
            with pytest.raises(_httpx.HTTPStatusError):
                c.fetch_month(2026, 9)

    def test_the_command_renews_and_retries_on_403(self, cookie_file, monkeypatch):
        from hilan.cli import _fetch_months
        from hilan.client import HilanClient

        state = {"n": 0}

        def handler(request):
            if request.url.path.endswith("/LoginApi/LoginRequest"):
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            if request.url.path == "/login":
                return httpx.Response(200, text='"OrgId":"1000"')
            if request.method == "POST":
                return httpx.Response(200, text=MONTH_PAGE)
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(403, text="nope")
            return httpx.Response(
                200,
                text='<html><div id="calendar_container"></div><form>'
                '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUB" />'
                "</form></html>",
            )

        monkeypatch.setattr("hilan.cli._authenticate", lambda c, force=False: None)
        with HilanClient(transport=httpx.MockTransport(handler)) as c:
            assert _fetch_months(c, [(2026, 9)]) == [MONTH_PAGE]


class TestBeingBlockedOutright:
    """A 403 on the login page is not a password problem and not a session one.

    Hilan sits behind a BIG-IP application firewall that answers a refused
    client with its own "403 Forbidden / Transaction ID" page, typically after
    too many requests. Retrying or logging in again cannot help, and saying
    "wrong password" would send the reader to change a password that is
    perfectly good.
    """

    BLOCK_PAGE = ("<html><head><title>403 Forbidden</title></head><body>"
                  "<h2>403 Forbidden</h2><h2>Transaction ID:</h2> 0000000"
                  "</body></html>")

    def _blocked(self):
        return httpx.MockTransport(
            lambda request: httpx.Response(403, text=self.BLOCK_PAGE)
        )

    def test_the_login_says_it_was_refused(self, cookie_file):
        from hilan.client import Blocked
        from hilan.config import Credentials

        with client(self._blocked()) as c:
            with pytest.raises(Blocked, match="refus"):
                c.login(Credentials(username="12345", password="x"))

    def test_it_mentions_waiting_rather_than_credentials(self, cookie_file):
        from hilan.client import Blocked
        from hilan.config import Credentials

        with client(self._blocked()) as c:
            with pytest.raises(Blocked) as refused:
                c.login(Credentials(username="12345", password="x"))
        assert "wait" in str(refused.value).lower()
        assert "password" not in str(refused.value).lower()

    def test_it_is_not_mistaken_for_a_bad_password(self, cookie_file):
        from hilan.client import Blocked, LoginFailed

        assert issubclass(Blocked, LoginFailed)
