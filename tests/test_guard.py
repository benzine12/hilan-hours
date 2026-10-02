"""The read-only guard must make a save physically impossible."""
import httpx
import pytest

from hilan.client import ReadOnlyViolation, build_client

ATTENDANCE = "https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx?isOnSelf=true"
LOGIN = "https://example.net.hilan.co.il/HilanCenter/Public/api/LoginApi/LoginRequest"

SAFE_BODY = {
    "__EVENTTARGET": "",
    "__calendarSelectedDays": "9740,9741",
    "ctl00$mp$currentMonth": "01/09/2026",
    "ctl00$mp$RefreshSelectedDays": "ימים נבחרים",
}


@pytest.fixture
def client():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    with build_client(transport=transport) as c:
        yield c


class TestAllowed:
    def test_get_attendance_page(self, client):
        assert client.get(ATTENDANCE).status_code == 200

    def test_login_post(self, client):
        assert client.post(LOGIN, data={"username": "x", "password": "y"}).status_code == 200

    def test_month_refresh_post(self, client):
        assert client.post(ATTENDANCE, data=SAFE_BODY).status_code == 200


class TestBlocked:
    @pytest.mark.parametrize(
        "field",
        [
            "ctl00$mp$RG_Days_100012345_2026_09$btnSave",
            "ctl00$mp$RG_Days_100012345_2026_09$btnClear",
            "ctl00$mp$RG_Days_100012345_2026_09$btnPickStepProject",
            "ctl00$mp$CollectiveAttendance",
        ],
    )
    def test_mutating_button_rejected(self, client, field):
        body = dict(SAFE_BODY, **{field: "x"})
        with pytest.raises(ReadOnlyViolation) as err:
            client.post(ATTENDANCE, data=body)
        assert field.rsplit("$", 1)[-1] in str(err.value)

    def test_rejected_even_when_url_encoded(self, client):
        # The dollar signs arrive percent-encoded on the wire.
        raw = "ctl00%24mp%24RG_Days_1_2026_09%24btnSave=%D7%A9%D7%9E%D7%99%D7%A8%D7%94"
        with pytest.raises(ReadOnlyViolation, match="mutating field 'btnSave'"):
            client.post(ATTENDANCE, content=raw.encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})

    def test_post_without_a_known_read_action_rejected(self, client):
        # Only fields a read sends, so nothing but the missing action can refuse it.
        with pytest.raises(ReadOnlyViolation, match="exactly one known read action"):
            client.post(ATTENDANCE, data={"__EVENTTARGET": "", "Time": "9"})

    def test_a_named_postback_target_is_refused(self, client):
        with pytest.raises(ReadOnlyViolation, match="postback target"):
            client.post(ATTENDANCE, data={**SAFE_BODY, "__EVENTTARGET": "ctl00$mp$Strip$x"})

    def test_two_read_actions_are_refused(self, client):
        with pytest.raises(ReadOnlyViolation, match="exactly one known read action"):
            client.post(ATTENDANCE, data={**SAFE_BODY, "ctl00$mp$RefreshPeriod": "x"})

    @pytest.mark.parametrize("port", [80, 444, 8443])
    def test_another_port_is_refused(self, client, port):
        with pytest.raises(ReadOnlyViolation, match="only https"):
            client.get(f"https://example.net.hilan.co.il:{port}/Hilannetv2/Attendance/calendarpage.aspx")

    def test_plain_http_is_refused(self, client):
        with pytest.raises(ReadOnlyViolation, match="only https"):
            client.get("http://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx")

    def test_head_is_a_read(self, client):
        assert client.head(ATTENDANCE).status_code == 200

    @pytest.mark.parametrize("path", ["/Hilannetv2/Attendance/calendarpage%2easpx",
                                      "/Hilannetv2//Attendance/calendarpage.aspx"])
    def test_an_unusual_path_is_refused(self, client, path):
        with pytest.raises(ReadOnlyViolation, match="unusual path"):
            client.post("https://example.net.hilan.co.il" + path, data=SAFE_BODY)

    def test_post_to_unknown_path_rejected(self, client):
        with pytest.raises(ReadOnlyViolation):
            client.post("https://example.net.hilan.co.il/Hilannetv2/Something/Else.aspx",
                        data=SAFE_BODY)

    def test_post_to_foreign_host_rejected(self, client):
        with pytest.raises(ReadOnlyViolation):
            client.post("https://example.com/", data=SAFE_BODY)

    @pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
    def test_mutating_methods_rejected(self, client, method):
        with pytest.raises(ReadOnlyViolation):
            client.request(method, ATTENDANCE)


class TestGetsStayOpen:
    def test_get_to_any_hilan_path_is_fine(self, client):
        assert client.get("https://example.net.hilan.co.il/Hilannetv2/ng/personal-file/home").status_code == 200

    def test_get_to_foreign_host_rejected(self, client):
        with pytest.raises(ReadOnlyViolation):
            client.get("https://example.com/")


class TestOnlyAFormBodyIsSent:
    """The checks read a form body; a body of any other kind would go unread."""

    def test_multipart_carrying_a_day_edit_is_refused(self, client):
        files = {"ctl00$mp$RG_Days_1$ctl02$ManualEntry": (None, "06:00")}
        with pytest.raises(ReadOnlyViolation, match="multipart"):
            client.post(ATTENDANCE, data=SAFE_BODY, files=files)

    def test_json_to_the_login_is_refused(self, client):
        with pytest.raises(ReadOnlyViolation, match="json"):
            client.post(LOGIN, json={"isChangePassword": True, "newPassword": "n"})

    def test_a_body_with_no_type_is_refused(self, client):
        with pytest.raises(ReadOnlyViolation, match="missing"):
            client.post(ATTENDANCE, content=b"ctl00%24mp%24RefreshSelectedDays=x")

    def test_the_charset_suffix_is_fine(self, client):
        response = client.post(
            ATTENDANCE, content=b"__calendarSelectedDays=9740&ctl00%24mp%24RefreshSelectedDays=x",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        assert response.status_code == 200


class TestTheQueryStringIsCheckedToo:
    """ASP.NET reads fields from the query string as well as from the body."""

    def test_a_save_button_in_the_query_of_a_read(self, client):
        with pytest.raises(ReadOnlyViolation, match="query string carries"):
            client.post(ATTENDANCE + "&ctl00$mp$RG$btnSave=Save", data=SAFE_BODY)

    def test_a_postback_target_in_the_query_of_a_read(self, client):
        with pytest.raises(ReadOnlyViolation, match="query string carries"):
            client.post(ATTENDANCE + "&__EVENTTARGET=x", data=SAFE_BODY)

    def test_anything_but_is_on_self_in_the_query_of_a_read(self, client):
        with pytest.raises(ReadOnlyViolation, match="query"):
            client.post(ATTENDANCE + "&other=1", data=SAFE_BODY)

    def test_a_password_change_in_the_query_of_a_login(self, client):
        with pytest.raises(ReadOnlyViolation, match="query"):
            client.post(LOGIN + "?isChangePassword=true&newPassword=n",
                        data={"username": "x", "password": "y"})

    @pytest.mark.parametrize("query", ["__VIEWSTATE=v", "__EVENTTARGET=x", "__eventargument=y",
                                       "__EVENTVALIDATION=e", "ctl00%24mp%24RG%24btnsave=1",
                                       "ctl00$mp$CollectiveAttendance=1"])
    def test_a_get_that_would_be_processed_as_a_postback(self, client, query):
        with pytest.raises(ReadOnlyViolation):
            client.get(ATTENDANCE + "&" + query)

    def test_an_ordinary_query_on_a_get_is_fine(self, client):
        url = "https://example.net.hilan.co.il/login?ReturnUrl=%2FHilannetv2%2FAttendance"
        assert client.get(url).status_code == 200


class TestTheLoginBody:
    """Only the fields the login sends, named exactly: Hilan ignores case."""

    @pytest.mark.parametrize("field", ["IsChangePassword", "ischangepassword", "NewPassword",
                                       "newpassword", "changePassword", "Password"])
    def test_a_field_the_login_never_sends(self, client, field):
        with pytest.raises(ReadOnlyViolation, match="never sends"):
            client.post(LOGIN, data={"username": "x", "password": "y", field: "true"})

    @pytest.mark.parametrize("body", [
        {"isChangePassword": "true"}, {"isChangePassword": "False"}, {"isChangePassword": ""},
        {"newPassword": "n"},
    ], ids=["true", "capital-false", "empty", "new-password"])
    def test_a_login_that_changes_the_password(self, client, body):
        with pytest.raises(ReadOnlyViolation, match="changes the password"):
            client.post(LOGIN, data={"username": "x", "password": "y", **body})

    @pytest.mark.parametrize("body", [
        {"isChangePassword": ["false", "true"]}, {"newPassword": ["", ""]}, {"newPassword": ["", "n"]},
    ], ids=["change-twice", "two-empty-new-passwords", "new-password-twice"])
    def test_a_field_sent_twice(self, client, body):
        """ASP.NET reads two empty newPassword fields as ",", which is not empty."""
        with pytest.raises(ReadOnlyViolation, match="sent twice"):
            client.post(LOGIN, data={"username": "x", "password": "y", **body})

    def test_a_field_sent_twice_in_a_read(self, client):
        with pytest.raises(ReadOnlyViolation, match="sent twice"):
            client.post(ATTENDANCE, headers={"Content-Type": "application/x-www-form-urlencoded"},
                        content=b"__EVENTTARGET=&__EVENTTARGET=&__calendarSelectedDays=9740"
                                b"&ctl00%24mp%24RefreshSelectedDays=x")

    @pytest.mark.parametrize("field", ["__VIEWSTATEFIELDCOUNT", "__VIEWSTATEGENERATOR", "__LASTFOCUS",
                                       "__SCROLLPOSITIONX"])
    def test_other_postback_fields_in_a_get(self, client, field):
        with pytest.raises(ReadOnlyViolation, match="query string carries"):
            client.get(f"https://example.net.hilan.co.il/Hilannetv2/Attendance/calendarpage.aspx?{field}=2")

    def test_the_login_with_a_verification_code(self, client):
        body = {"orgId": "1", "username": "x", "password": "y", "isChangePassword": "false",
                "newPassword": "", "id": "", "isEn": "false", "verificationCode": "123456",
                "saveBrowser": "true"}
        assert client.post(LOGIN, data=body).status_code == 200


class TestCaseDoesNotHideAButton:
    @pytest.mark.parametrize("field", ["ctl00$mp$RG$BTNSAVE", "ctl00$mp$rg$btnclear",
                                       "ctl00$mp$collectiveattendance"])
    def test_in_a_field_name(self, client, field):
        with pytest.raises(ReadOnlyViolation, match="mutating field"):
            client.post(ATTENDANCE, data={**SAFE_BODY, field: "x"})

    def test_in_a_value(self, client):
        with pytest.raises(ReadOnlyViolation, match="btnSave"):
            client.post(ATTENDANCE, data={**SAFE_BODY, "__EVENTARGUMENT": "ctl00$mp$RG$BtnSave"})
