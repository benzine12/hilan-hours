# -*- coding: utf-8 -*-
"""The widget's network code, held to the same rules as client.py.

The phone is where these would otherwise first be tried: what it logs in on,
what it does when Hilan turns the password down, which redirects it follows,
which cookies it sends where, and which day it believes it is.
"""
import json
import re
from urllib.parse import parse_qs

import pytest

from phone import NODE, SITE, phone

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

ATT = "/Hilannetv2/Attendance/calendarpage.aspx"
LOGIN_API = "/HilanCenter/Public/api/LoginApi/LoginRequest"
FORM = ('<html><table id="calendar_container"></table>'
        '<input type="hidden" name="__VIEWSTATE" value="vs">'
        '<input type="hidden" name="__EVENTVALIDATION" value="ev&amp;1"></html>')
LOGIN_PAGE = '<html><script>var initialData = {"OrgId":"777"};</script></html>'
SIGNED_OUT = {"status": 200, "url": "/login", "body": "<html>sign in</html>"}
ACCEPTED = {"body": json.dumps({"IsFail": False})}
REFUSED = {"body": json.dumps({"IsFail": True, "Code": 1, "ErrorMessage": "wrong password"})}
WANTS_CODE = {"body": json.dumps({"IsFail": True, "IsShowVerificationCode": True})}
KEYS = {"hilan_username": "12345", "hilan_password": "test-only-pass"}
WIDGET = {"runsInWidget": True, "runsInApp": False, "widgetFamily": "medium"}
APP = {"runsInWidget": False, "runsInApp": True}
SIRI = {"runsInWidget": False, "runsInApp": False, "runsWithSiri": True}
REFUSED_MARK = "hilan_login_refused"
COOKIES = "hilan_cookies.json"
HOST = "example.net.hilan.co.il"
CACHE = "hilan_last_analysis.json"
SEPT = ["fetchMonth", 2026, 9]


def routes(month_page, attendance=(FORM,), login=(ACCEPTED,), post=None):
    """Hilan, answering GETs of the attendance page in turn."""
    as_response = lambda r: r if isinstance(r, dict) else {"body": r}
    return [
        {"method": "GET", "path": ATT, "responses": [as_response(r) for r in attendance]},
        {"method": "GET", "path": "/login", "responses": [{"body": LOGIN_PAGE}]},
        {"method": "POST", "path": LOGIN_API, "responses": list(login)},
        {"method": "POST", "path": ATT, "responses": post or [{"body": month_page}]},
    ]


def sent(result, method, path):
    return [r for r in result["requests"]
            if r["method"] == method and r["url"].split("?")[0] == SITE + path]


def form(request):
    return {k: v[0] for k, v in parse_qs(request["body"], keep_blank_values=True).items()}


class TestLoggingIn:
    def test_a_live_session_is_used_as_it_is(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS, "call": SEPT})
        assert result["error"] is None
        assert sent(result, "POST", LOGIN_API) == []
        assert "calendar_container" in result["page"]

    def test_the_month_read_hands_back_event_validation_decoded(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS, "call": SEPT})
        body = form(sent(result, "POST", ATT)[0])
        assert body["__EVENTVALIDATION"] == "ev&1"
        assert body["ctl00$mp$currentMonth"] == "01/09/2026"

    def test_a_server_error_is_not_a_reason_to_send_the_password(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"status": 500}]),
                                  "keychain": KEYS, "call": SEPT})
        assert "500" in result["error"]["message"]
        assert sent(result, "POST", LOGIN_API) == []

    @pytest.mark.parametrize("gone", [SIGNED_OUT, {"status": 401}, {"status": 403}],
                             ids=["sent-to-login", "401", "403"])
    def test_a_lapsed_session_logs_in_once(self, tmp_path, september_html, gone):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[gone, FORM]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"] is None
        logins = sent(result, "POST", LOGIN_API)
        assert len(logins) == 1
        body = form(logins[0])
        assert body["orgId"] == "777" and body["username"] == "12345"
        assert body["isChangePassword"] == "false" and body["newPassword"] == ""

    def test_a_page_without_the_calendar_logs_in(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=["<html>maintenance</html>", FORM]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"] is None and len(sent(result, "POST", LOGIN_API)) == 1

    def test_a_session_that_will_not_stay_is_reported_not_retried(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "call": SEPT})
        assert "did not keep the session" in result["error"]["message"]
        assert len(sent(result, "POST", LOGIN_API)) == 1

    def test_a_month_that_does_not_come_back_is_an_error(self, tmp_path):
        result = phone(tmp_path, {"routes": routes("<html>something else</html>"),
                                  "keychain": KEYS, "call": SEPT})
        assert "did not come back" in result["error"]["message"]

    def test_the_login_page_without_an_org_id_still_logs_in(self, tmp_path, september_html):
        scenario = {"routes": routes(september_html, attendance=[SIGNED_OUT, FORM]),
                    "keychain": KEYS, "call": SEPT}
        scenario["routes"][1]["responses"] = [{"body": "<html></html>"}]
        result = phone(tmp_path, scenario)
        assert form(sent(result, "POST", LOGIN_API)[0])["orgId"] == ""

    def test_the_firewall_is_named(self, tmp_path, september_html):
        scenario = {"routes": routes(september_html, attendance=[SIGNED_OUT]), "keychain": KEYS, "call": SEPT}
        scenario["routes"][1]["responses"] = [{"status": 403}]
        result = phone(tmp_path, scenario)
        assert "firewall" in result["error"]["message"]
        assert sent(result, "POST", LOGIN_API) == []

    @pytest.mark.parametrize("answer,said", [
        ({"body": "<html>maintenance"}, "without JSON"),
        ({"status": 502, "body": "<html>bad gateway"}, "answered 502"),
        # A server error is not a refusal, whatever its body says.
        ({"status": 503, "body": json.dumps({"IsFail": True, "Code": 1})}, "answered 503"),
        ({"status": 500, "body": json.dumps({"Message": "An error has occurred."})}, "answered 500"),
    ], ids=["not-json", "502", "503-saying-refused", "500-json"])
    def test_an_answer_that_is_not_a_verdict_is_an_error_not_a_refusal(self, tmp_path, september_html,
                                                                        answer, said):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT], login=[answer]),
                                  "keychain": KEYS, "call": SEPT})
        assert said in result["error"]["message"]
        assert result["error"]["name"] == "Error"
        assert REFUSED_MARK not in result["files"]
        assert result["keychain"]["hilan_password"] == KEYS["hilan_password"]

    @pytest.mark.parametrize("answer", [{}, {"Success": False}, {"IsFail": "false"}, {"IsFail": None}, []],
                             ids=["empty", "other-shape", "string", "null", "list"])
    def test_an_answer_of_another_shape_is_not_taken_for_success(self, tmp_path, september_html, answer):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT, FORM],
                                                   login=[{"body": json.dumps(answer)}]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"]["name"] == "LoginRefused"
        assert "does not recognise" in result["error"]["message"]
        assert REFUSED_MARK in result["files"]

    def test_a_page_that_is_not_the_attendance_page_logs_in(self, tmp_path, september_html):
        # The calendar marker alone is not enough: the request must have landed on the page.
        elsewhere = {"url": "/Hilannetv2/Home.aspx", "body": FORM}
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[elsewhere, FORM]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"] is None and len(sent(result, "POST", LOGIN_API)) == 1

    @pytest.mark.parametrize("config,timeout", [(WIDGET, 20), (APP, 90)], ids=["widget", "app"])
    def test_a_widget_gives_up_in_time_to_show_the_cache(self, tmp_path, september_html, config, timeout):
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                  "config": config, "call": SEPT})
        assert {r["timeout"] for r in result["requests"]} == {timeout}


class TestARefusedPassword:
    """Retrying a refused password on every refresh is how an account gets locked."""

    def refused(self, tmp_path, html, **extra):
        return phone(tmp_path, {"routes": routes(html, attendance=[SIGNED_OUT], login=[REFUSED]),
                                "keychain": KEYS, "call": SEPT, **extra})

    def test_it_is_reported_as_refused(self, tmp_path, september_html):
        result = self.refused(tmp_path, september_html)
        assert result["error"] == {"name": "LoginRefused", "message": "wrong password"}

    def test_the_password_is_forgotten(self, tmp_path, september_html):
        keychain = self.refused(tmp_path, september_html)["keychain"]
        assert "hilan_password" not in keychain
        assert keychain["hilan_username"] == "12345"

    def test_it_is_remembered(self, tmp_path, september_html):
        assert REFUSED_MARK in self.refused(tmp_path, september_html)["files"]

    def test_the_widget_does_not_try_again(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {REFUSED_MARK: "wrong password"},
                                  "config": WIDGET, "call": SEPT})
        assert result["error"]["name"] == "LoginRefused"
        assert "run the script in Scriptable" in result["error"]["message"]
        assert sent(result, "POST", LOGIN_API) == []

    def test_the_app_asks_again_and_then_clears_it(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT, FORM]),
                                  "keychain": {"hilan_username": "12345"},
                                  "files": {REFUSED_MARK: "wrong password"}, "config": APP,
                                  "alert": {"choice": 0, "fields": ["12345", " new pass "]}, "call": SEPT})
        assert result["error"] is None
        assert "refused" in result["alerts"][0]["message"]
        assert REFUSED_MARK not in result["files"]
        # Typed as is: spaces in a password are part of it.
        assert result["keychain"]["hilan_password"] == " new pass "
        assert form(sent(result, "POST", LOGIN_API)[0])["password"] == " new pass "

    def test_cancelling_sends_nothing(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": {"hilan_username": "12345"},
                                  "files": {REFUSED_MARK: "x"}, "config": APP,
                                  "alert": {"choice": -1}, "call": SEPT})
        assert result["error"]["message"] == "Login cancelled"
        assert sent(result, "POST", LOGIN_API) == []



class TestAVerificationCode:
    """Each automatic try would send another SMS; the widget cannot type the code."""

    HOLD = json.dumps({"kind": "verification", "reason": "code sent"})

    def asked(self, tmp_path, html, config=WIDGET, **extra):
        return phone(tmp_path, {"routes": routes(html, attendance=[SIGNED_OUT], login=[WANTS_CODE]),
                                "keychain": KEYS, "config": config, "call": SEPT, **extra})

    def test_the_widget_stops_and_says_so(self, tmp_path, september_html):
        result = self.asked(tmp_path, september_html)
        assert result["error"]["name"] == "LoginRefused"
        assert "verification code" in result["error"]["message"]
        assert json.loads(result["files"][REFUSED_MARK])["kind"] == "verification"

    def test_it_is_not_a_refused_password(self, tmp_path, september_html):
        assert self.asked(tmp_path, september_html)["keychain"]["hilan_password"] == KEYS["hilan_password"]

    def test_the_next_refresh_sends_nothing(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {REFUSED_MARK: self.HOLD}, "call": SEPT})
        assert "verification code" in result["error"]["message"]
        assert sent(result, "POST", LOGIN_API) == []

    def test_siri_sends_nothing_either(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {REFUSED_MARK: self.HOLD}, "config": SIRI})
        assert sent(result, "POST", LOGIN_API) == []
        assert "verification code" in result["output"]

    def test_the_app_asks_for_the_code_and_sends_it(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT, FORM],
                                                   login=[WANTS_CODE, ACCEPTED]),
                                  "keychain": KEYS, "files": {REFUSED_MARK: self.HOLD}, "config": APP,
                                  "alert": {"choice": 0, "fields": [" 123456 "]}, "call": SEPT})
        assert result["error"] is None
        assert result["alerts"][0]["title"] == "Hilan Verification"
        second = form(sent(result, "POST", LOGIN_API)[1])
        assert second["verificationCode"] == "123456" and second["saveBrowser"] == "true"
        assert REFUSED_MARK not in result["files"]

    def test_a_wrong_code_keeps_the_password_and_the_stop(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT],
                                                   login=[WANTS_CODE, REFUSED]),
                                  "keychain": KEYS, "config": APP,
                                  "alert": {"choice": 0, "fields": ["000000"]}, "call": SEPT})
        assert "did not accept the code" in result["error"]["message"]
        assert result["keychain"]["hilan_password"] == KEYS["hilan_password"]
        assert json.loads(result["files"][REFUSED_MARK])["kind"] == "verification"

    def test_cancelling_the_code_keeps_the_stop(self, tmp_path, september_html):
        result = self.asked(tmp_path, september_html, config=APP, alert={"choice": -1})
        assert result["error"]["message"] == "Login cancelled"
        assert len(sent(result, "POST", LOGIN_API)) == 1
        assert json.loads(result["files"][REFUSED_MARK])["kind"] == "verification"

    def test_the_home_screen_says_a_code_is_needed(self, tmp_path, september_html):
        first = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                 "now": "2026-09-22T09:00:00Z"})
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {**first["files"], REFUSED_MARK: self.HOLD},
                                  "now": "2026-09-22T10:00:00Z"})
        assert "code needed" in " | ".join(result["texts"])

    def test_the_home_screen_says_so_over_the_cached_figure(self, tmp_path, september_html):
        first = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                 "now": "2026-09-22T09:00:00Z"})
        assert CACHE in first["files"]
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT], login=[REFUSED]),
                                  "keychain": KEYS, "files": first["files"], "now": "2026-09-22T10:00:00Z"})
        shown = " | ".join(result["texts"])
        assert "as of 12:00" in shown and "password refused" in shown, shown


class TestUnauthorised:
    def test_a_401_from_the_login_is_a_refusal(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT],
                                                   login=[{"status": 401, "body": ""}]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"]["name"] == "LoginRefused"
        assert REFUSED_MARK in result["files"] and "hilan_password" not in result["keychain"]


class TestRedirects:
    """Every hop is checked like client.py checks each request."""

    @pytest.mark.parametrize("to", [
        "https://elsewhere.example.com/Hilannetv2/Attendance/calendarpage.aspx",
        "http://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx",
        "https://example.net.hilan.co.il:8443/Hilannetv2/Attendance/calendarpage.aspx",
        "https://example.net.hilan.co.il.elsewhere.com/",
        "https://other.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx",
        "javascript:alert(1)",
        "https://example.net.hilan.co.il:@elsewhere.example.com/Hilannetv2/Attendance/calendarpage.aspx",
        "https://example.net.hilan.co.il:443@elsewhere.example.com/",
        "https://example.net.hilan.co.il\\@elsewhere.example.com/",
        "https://user@example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx",
        "https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx?__VIEWSTATE=x",
        "https://example.net.hilan.co.il/Hilannetv2/x.aspx?ctl00%24mp%24btnSave=1",
        "https://example.net.hilan.co.il/Hilannetv2/x.aspx?__%u0056IEWSTATE=x",
        "https://example.net.hilan.co.il/Hilannetv2/x.aspx?__CALLBACKID=grid",
        "https://example.net.hilan.co.il/Hilannetv2/x.aspx?next=ctl00$mp$RG$btnSave",
    ], ids=["other-site", "http", "port", "lookalike", "other-company", "not-a-url",
            "empty-port-userinfo", "port-userinfo", "backslash", "userinfo", "postback-query", "save-query",
            "percent-u", "callback", "save-in-a-value"])
    def test_away_from_the_site_is_refused(self, tmp_path, september_html, to):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"redirect": {"to": to}}]),
                                  "keychain": KEYS, "files": {COOKIES: json.dumps([{"name": "sid", "value": "s", "path": "/", "host": HOST}])},
                                  "call": SEPT})
        assert "Guard refused a redirect" in result["error"]["message"]
        assert result["requests"][0]["redirect"]["followed"] is False
        assert sent(result, "POST", LOGIN_API) == []

    def test_within_the_site_it_is_followed_with_the_cookies(self, tmp_path, september_html):
        to = SITE + ATT + "?isOnSelf=true&again=1"
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"redirect": {"to": to}, "body": FORM}]),
                                  "keychain": KEYS, "files": {COOKIES: json.dumps([{"name": "sid", "value": "s", "path": "/", "host": HOST}])},
                                  "call": SEPT})
        assert result["error"] is None
        assert result["requests"][0]["redirect"] == {"to": to, "followed": True, "cookie": "sid=s"}

    def test_the_host_is_compared_whatever_its_case(self, tmp_path, september_html):
        to = "https://EXAMPLE.net.hilan.co.il" + ATT + "?isOnSelf=true"
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"redirect": {"to": to}, "body": FORM}]),
                                  "keychain": KEYS, "call": SEPT})
        assert result["error"] is None and result["requests"][0]["redirect"]["followed"] is True

    def test_a_post_carried_to_another_page_is_refused(self, tmp_path, september_html):
        hop = {"redirect": {"to": SITE + "/Hilannetv2/Attendance/Other.aspx", "method": "POST"}}
        result = phone(tmp_path, {"routes": routes(september_html, post=[hop]), "keychain": KEYS, "call": SEPT})
        assert "Guard refused a redirect" in result["error"]["message"]

    def test_a_post_carried_to_the_same_page_is_followed(self, tmp_path, september_html):
        hop = {"redirect": {"to": SITE + ATT + "?isOnSelf=true", "method": "POST"}, "body": september_html}
        result = phone(tmp_path, {"routes": routes(september_html, post=[hop]), "keychain": KEYS, "call": SEPT})
        assert result["error"] is None

    def test_a_post_that_lands_somewhere_else_is_not_taken_for_the_month(self, tmp_path, september_html):
        hop = {"redirect": {"to": SITE + "/Hilannetv2/Home.aspx", "method": "GET"}, "body": september_html}
        result = phone(tmp_path, {"routes": routes(september_html, post=[hop]), "keychain": KEYS, "call": SEPT})
        assert "did not come back" in result["error"]["message"]


class TestCookies:
    def run(self, tmp_path, html, saved, **response):
        if isinstance(saved, list):
            saved = [{"host": HOST, **c} for c in saved]
        stored = saved if isinstance(saved, str) else json.dumps(saved)
        return phone(tmp_path, {"routes": routes(html, attendance=[{"body": FORM, **response}]),
                                "keychain": KEYS, "files": {COOKIES: stored}, "call": SEPT})

    def cookie_header(self, result):
        return result["requests"][0]["headers"].get("Cookie", "")

    def test_same_names_on_two_paths_both_go_the_longer_first(self, tmp_path, september_html):
        """What a browser sends (RFC 6265 5.4): the server picks, not the widget."""
        result = self.run(tmp_path, september_html, [{"name": "TS", "value": "root", "path": "/"},
                                                     {"name": "TS", "value": "deep", "path": "/Hilannetv2"}])
        assert self.cookie_header(result) == "TS=deep; TS=root"

    def test_an_expires_in_the_past_deletes_one(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [{"name": "sid", "value": "s", "path": "/"}],
                          headers={"Set-Cookie": "sid=deleted; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/"})
        assert all(c["name"] != "sid" for c in json.loads(result["files"][COOKIES]))

    def test_a_cookie_for_another_path_stays_home(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [{"name": "H", "value": "x", "path": "/HilanCenter"},
                                                     {"name": "sid", "value": "s", "path": "/"}])
        assert self.cookie_header(result) == "sid=s"

    def test_a_path_is_matched_by_whole_segments(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [{"name": "H", "value": "x", "path": "/Hilan"},
                                                     {"name": "sid", "value": "s", "path": "/"}])
        assert self.cookie_header(result) == "sid=s"

    def test_a_cookie_without_a_path_belongs_to_the_requests_folder(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [], headers={"Set-Cookie": "a=1"})
        assert json.loads(result["files"][COOKIES]) == [
            {"name": "a", "value": "1", "path": "/Hilannetv2/Attendance", "host": HOST}]

    def test_max_age_zero_deletes_one(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [{"name": "sid", "value": "s", "path": "/"}],
                          headers={"Set-Cookie": "sid=gone; Max-Age=0; Path=/"})
        assert all(c["name"] != "sid" for c in json.loads(result["files"][COOKIES]))

    @pytest.mark.parametrize("domain", ["elsewhere.example.com", ".hilan.co.il.elsewhere.com", "other.net.hilan.co.il"])
    def test_a_cookie_for_another_domain_is_not_kept(self, tmp_path, september_html, domain):
        result = self.run(tmp_path, september_html, [],
                          cookies=[{"name": "sid", "value": "ATTACKER", "path": "/", "domain": domain}],
                          headers={"Set-Cookie": f"x=1; Domain={domain}; Path=/"})
        assert COOKIES not in result["files"] or json.loads(result["files"][COOKIES]) == []

    @pytest.mark.parametrize("domain", ["example.net.hilan.co.il", ".net.hilan.co.il", "hilan.co.il"])
    def test_a_cookie_for_this_site_or_its_parents_is_kept(self, tmp_path, september_html, domain):
        result = self.run(tmp_path, september_html, [],
                          cookies=[{"name": "sid", "value": "s", "path": "/", "domain": domain}])
        assert json.loads(result["files"][COOKIES]) == [{"name": "sid", "value": "s", "path": "/", "host": HOST}]

    def test_a_cookie_from_another_companys_site_is_not_sent(self, tmp_path, september_html):
        stored = json.dumps([{"name": "sid", "value": "theirs", "path": "/", "host": "other.net.hilan.co.il"},
                             {"name": "TS", "value": "untagged", "path": "/"}])
        assert self.cookie_header(self.run(tmp_path, september_html, stored)) == ""

    @pytest.mark.parametrize("broken", ["not json", "[1, null, {\"value\": 3}]", "42", "{\"sid\": \"abc\"}"])
    def test_a_broken_file_is_no_cookies(self, tmp_path, september_html, broken):
        result = self.run(tmp_path, september_html, broken)
        assert result["error"] is None and self.cookie_header(result) == ""

    def test_new_ones_are_kept_with_their_path(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [],
                          cookies=[{"name": "TS", "value": "1", "path": "/Hilannetv2"}])
        assert {"name": "TS", "value": "1", "path": "/Hilannetv2", "host": HOST} in json.loads(result["files"][COOKIES])

    def test_an_empty_value_clears_one(self, tmp_path, september_html):
        result = self.run(tmp_path, september_html, [{"name": "sid", "value": "s", "path": "/"}],
                          cookies=[{"name": "sid", "value": "", "path": "/"}])
        assert all(c["name"] != "sid" for c in json.loads(result["files"][COOKIES]))

    def test_set_cookie_headers_are_split_between_cookies_not_inside_dates(self, tmp_path, september_html):
        header = "a=1; expires=Wed, 21 Oct 2026 07:28:00 GMT; path=/, b=2; Path=/Hilannetv2; HttpOnly"
        result = self.run(tmp_path, september_html, [], headers={"Set-Cookie": header})
        stored = json.loads(result["files"][COOKIES])
        assert {"name": "a", "value": "1", "path": "/", "host": HOST} in stored
        assert {"name": "b", "value": "2", "path": "/Hilannetv2", "host": HOST} in stored
        assert len(stored) == 2


class TestIsraelTime:
    """Hilan's day is Israel's: a phone abroad must not read the wrong one."""

    @pytest.mark.parametrize("tz", ["America/New_York", "Asia/Tokyo", "UTC", "Asia/Jerusalem"])
    def test_the_clock_reads_israel_time_wherever_the_phone_is(self, tmp_path, tz):
        result = phone(tmp_path, {"now": "2026-09-30T22:30:00Z", "call": ["israelNow"]}, tz=tz)
        assert result["page"] == "2026-10-01 01:30"

    def test_winter_time_too(self, tmp_path):
        result = phone(tmp_path, {"now": "2026-12-31T22:30:00Z", "call": ["israelNow"]}, tz="UTC")
        assert result["page"] == "2027-01-01 00:30"

    @pytest.mark.parametrize("tz,instant", [
        ("Europe/Berlin", "2026-03-28T23:30:00Z"),       # Berlin springs forward at 02:00 on 29/03
        ("America/New_York", "2026-03-08T00:30:00Z"),    # New York springs forward at 02:00 on 08/03
    ])
    def test_an_hour_the_phones_own_clock_skips(self, tmp_path, tz, instant):
        """02:30 in Israel does not exist on the phone's clock that night; it is still 02:30."""
        result = phone(tmp_path, {"now": instant, "call": ["israelNow"]}, tz=tz)
        assert result["page"].endswith(" 02:30"), result["page"]

    def test_the_month_fetched_is_israels(self, tmp_path, september_html):
        # 23:00 on 30/09 in Israel is already 1 October in Tokyo.
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                  "now": "2026-09-30T20:00:00Z"}, tz="Asia/Tokyo")
        assert form(sent(result, "POST", ATT)[0])["ctl00$mp$currentMonth"] == "01/09/2026"

    def test_a_cached_figure_is_dated_in_israel_time(self, tmp_path):
        result = phone(tmp_path, {"now": "2026-09-22T12:00:00Z",
                                  "call": ["staleLabel", {"stale": {"since": "2026-09-22T11:02:00Z"}}]},
                       tz="America/Los_Angeles")
        assert result["page"] == "as of 14:02"

    def test_a_cached_figure_from_another_day(self, tmp_path):
        result = phone(tmp_path, {"now": "2026-09-23T12:00:00Z",
                                  "call": ["staleLabel", {"stale": {"since": "2026-09-22T11:02:00Z"}}]})
        assert result["page"] == "as of 22/09 14:02"

    @pytest.mark.parametrize("since", [None, "not a date"])
    def test_an_unknown_time_is_said_plainly(self, tmp_path, since):
        result = phone(tmp_path, {"call": ["staleLabel", {"stale": {"since": since}}]})
        assert result["page"] == "as of an earlier run"


class TestTheScreen:
    def show(self, tmp_path, html, family="medium", **extra):
        return phone(tmp_path, {"routes": routes(html), "keychain": KEYS, "now": "2026-09-22T12:00:00Z",
                                "config": {**WIDGET, "widgetFamily": family}, **extra})

    def test_dates_read_day_first(self, tmp_path, september_html):
        assert "settled to 21/09" in self.show(tmp_path, september_html)["texts"]

    def test_ios_is_asked_for_a_fresh_figure(self, tmp_path, september_html):
        assert self.show(tmp_path, september_html)["refreshAfter"] == "2026-09-22T12:15:00.000Z"

    def test_the_round_lock_screen_widget_shows_the_balance(self, tmp_path, september_html):
        texts = self.show(tmp_path, september_html, "accessoryCircular")["texts"]
        assert texts[0] == "NOW" and len(texts) == 2 and re.fullmatch(r"[+-]?\d+\.\d\d", texts[1])

    @pytest.mark.parametrize("family", ["large", "extraLarge"])
    def test_the_large_ones_say_when_it_was_worked_out(self, tmp_path, september_html, family):
        assert "worked out at 15:00" in self.show(tmp_path, september_html, family)["texts"]

    def test_the_figure_is_saved_for_when_hilan_is_down(self, tmp_path, september_html):
        files = self.show(tmp_path, september_html)["files"]
        saved = json.loads(files[CACHE])
        assert saved["cachedAt"] == "2026-09-22T12:00:00.000Z" and saved["computedAt"] == "15:00"

    @pytest.mark.parametrize("cache", ["not json", "{}", json.dumps({"live": {}}), "null",
                                       json.dumps({"live": {}, "today": {}, "banked": {}, "monthTotals": {}}),
                                       json.dumps({"live": {"balance": "1:00"}, "today": {}, "banked": {},
                                                   "monthTotals": {}, "cachedAt": "never"})])
    def test_a_broken_cache_shows_the_real_error(self, tmp_path, cache):
        result = phone(tmp_path, {"routes": routes("", attendance=[{"fail": "The network connection was lost."}]),
                                  "keychain": KEYS, "files": {CACHE: cache}, "config": WIDGET})
        assert result["texts"] == ["The network connection was lost."]


class TestSiri:
    def test_it_says_what_went_wrong(self, tmp_path):
        result = phone(tmp_path, {"routes": routes("", attendance=[{"fail": "The network connection was lost."}]),
                                  "keychain": KEYS, "config": SIRI})
        assert result["spoken"] == ["Hilan: The network connection was lost."]
        assert result["output"] == "Error: The network connection was lost."

    def test_on_a_cached_figure_it_says_why_and_leaves_out_an_earlier_day(self, tmp_path, september_html):
        first = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                 "now": "2026-09-22T09:00:00Z"})
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {**first["files"], REFUSED_MARK: "x"},
                                  "now": "2026-09-25T09:00:00Z", "config": SIRI})
        said = result["output"]
        assert "as of 22/09 12:00" in said and "refused the password" in said
        assert "today" not in said and "leave" not in said

    def test_on_a_cached_figure_from_today_it_still_says_today(self, tmp_path, september_html):
        first = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                 "now": "2026-09-22T09:00:00Z"})
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{"fail": "offline"}]),
                                  "keychain": KEYS, "files": first["files"],
                                  "now": "2026-09-22T10:00:00Z", "config": SIRI})
        said = result["output"]
        assert said.startswith("Hilan could not be reached.") and "as of 12:00" in said and "today" in said

    def test_it_says_the_balance(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                  "now": "2026-09-22T12:00:00Z", "config": SIRI})
        assert len(result["spoken"]) == 1 and result["spoken"][0] == result["output"]
        assert result["output"].startswith("You are ")


class TestARefreshThatGoesWrong:
    def test_a_cookie_ios_reports_as_expired_is_deleted(self, tmp_path, september_html):
        stored = json.dumps([{"name": "sid", "value": "s", "path": "/", "host": HOST}])
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[{
            "body": FORM, "cookies": [{"name": "sid", "value": "deleted", "path": "/",
                                       "expiresDate": "1970-01-01T00:00:00Z"}]}]),
            "keychain": KEYS, "files": {COOKIES: stored}, "call": SEPT})
        assert all(c["name"] != "sid" for c in json.loads(result["files"][COOKIES]))

    def test_a_new_password_lifts_the_stop_even_if_hilan_then_errs(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT],
                                                   login=[{"status": 500, "body": ""}]),
                                  "keychain": {"hilan_username": "12345"},
                                  "files": {REFUSED_MARK: "x"}, "config": APP,
                                  "alert": {"choice": 0, "fields": ["12345", "new pass"]}, "call": SEPT})
        assert "answered 500" in result["error"]["message"]
        assert REFUSED_MARK not in result["files"]
        assert result["keychain"]["hilan_password"] == "new pass"

    def test_a_slow_network_gives_up_within_a_widgets_time(self, tmp_path, september_html):
        first = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                 "now": "2026-09-22T09:00:00Z"})
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT, FORM]),
                                  "keychain": KEYS, "files": first["files"], "requestSeconds": 9,
                                  "now": "2026-09-22T10:00:00Z"})
        # Each request starts 9 s after the one before, and none may be allowed
        # to run past 25 s from the start of the refresh.
        timeouts = [r["timeout"] for r in result["requests"]]
        assert all(9 * i + t <= 25 for i, t in enumerate(timeouts)) and len(timeouts) <= 3, timeouts
        assert "as of" in " | ".join(result["texts"])           # the cached figure, marked

    def test_siri_waits_until_it_has_spoken(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html), "keychain": KEYS,
                                  "now": "2026-09-22T12:00:00Z", "config": SIRI})
        assert len(result["spoken"]) == 1


class TestLockScreenSizes:
    def cached(self, tmp_path, html, family, extra_now="2026-09-22T10:00:00Z"):
        first = phone(tmp_path, {"routes": routes(html), "keychain": KEYS, "now": "2026-09-22T09:00:00Z"})
        return phone(tmp_path, {"routes": routes(html, attendance=[{"fail": "offline"}]),
                                "keychain": KEYS, "files": first["files"], "now": extra_now,
                                "config": {**WIDGET, "widgetFamily": family}})

    def test_the_round_one_keeps_to_its_label_and_number(self, tmp_path, september_html):
        texts = self.cached(tmp_path, september_html, "accessoryCircular")["texts"]
        assert texts[0] == "⚠" and len(texts) == 2, texts

    def test_the_rectangular_one_keeps_to_three_lines(self, tmp_path, september_html):
        texts = self.cached(tmp_path, september_html, "accessoryRectangular")["texts"]
        assert len(texts) <= 3 and not any(t.startswith("Leave") for t in texts), texts

    @pytest.mark.parametrize("family", ["accessoryCircular", "accessoryInline", "accessoryRectangular"])
    def test_an_error_is_a_word_not_a_sentence(self, tmp_path, family):
        result = phone(tmp_path, {"routes": routes("", attendance=[{"fail": "The network connection was lost."}]),
                                  "keychain": KEYS, "config": {**WIDGET, "widgetFamily": family}})
        assert result["texts"] == ["⚠ Hilan"]

    def test_a_refused_password_says_so_in_a_word(self, tmp_path, september_html):
        result = phone(tmp_path, {"routes": routes(september_html, attendance=[SIGNED_OUT]),
                                  "keychain": KEYS, "files": {REFUSED_MARK: "x"},
                                  "config": {**WIDGET, "widgetFamily": "accessoryInline"}})
        assert result["texts"] == ["⚠ password refused"]
