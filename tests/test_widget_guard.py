# -*- coding: utf-8 -*-
"""The widget's read-only guard has to be as strict as the command line's.

Refusing the four mutating field names is not enough. Like client.py, it also
refuses a POST anywhere but the login endpoint and the attendance page, and an
attendance POST that does not carry a known read action — so a request that is
not recognisably a read never leaves, rather than only one that is recognisably
a write.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WIDGET = Path(__file__).resolve().parent.parent / "ios" / "HilanWidget.js"

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const [widgetPath, path, body] = process.argv.slice(1);
const code = fs.readFileSync(widgetPath, 'utf-8').replace(/^await run\(\);\s*$/m, '');
let sent = false;
const ctx = { console: { log() {}, error() {} }, config: {},
  FileManager: { local: () => ({ documentsDirectory: () => '/tmp', joinPath: (a, b) => a + '/' + b,
    fileExists: () => false, readString: () => '', writeString: () => {} }) },
  Keychain: {}, Script: {}, Alert: class {}, ListWidget: class {}, Color: class {}, Font: {},
  Request: class { constructor(u) { this.url = u; this.headers = {}; }
    loadString() { sent = true; this.response = { statusCode: 200, headers: {} }; return Promise.resolve('ok'); } },
};
vm.createContext(ctx);
vm.runInContext(code + '\nglobalThis.__Client = HilanClient;', ctx);
new ctx.__Client().post(path, JSON.parse(body))
  .then(() => process.stdout.write(JSON.stringify({ sent, refused: null })))
  .catch(e => process.stdout.write(JSON.stringify({ sent, refused: e.message })));
"""

ATTENDANCE = "/Hilannetv2/Attendance/calendarpage.aspx?isOnSelf=true"
LOGIN = "/HilanCenter/Public/api/LoginApi/LoginRequest"
READ = {"__calendarSelectedDays": "9740", "ctl00$mp$RefreshSelectedDays": "ימים נבחרים"}

# Found once, by full path: on Windows a bare "node" is not always resolved.
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def post(path, body):
    out = subprocess.run([NODE, "-e", RUNNER, str(WIDGET), path, json.dumps(body)],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


class TestWhatIsAllowed:
    def test_a_month_read(self):
        assert post(ATTENDANCE, READ) == {"sent": True, "refused": None}

    def test_a_login(self):
        assert post(LOGIN, {"username": "x", "password": "y"})["sent"] is True


class TestWhatNeverLeaves:
    @pytest.mark.parametrize("field", [
        "ctl00$mp$RG_Days_100012345_2026_09$btnSave",
        "ctl00$mp$RG_Days_100012345_2026_09$btnClear",
        "ctl00$mp$RG_Days_100012345_2026_09$btnPickStepProject",
        "ctl00$mp$CollectiveAttendance",
    ])
    def test_a_mutating_field(self, field):
        result = post(ATTENDANCE, {**READ, field: "x"})
        assert result["sent"] is False and result["refused"]

    def test_an_attendance_post_with_no_read_action(self):
        result = post(ATTENDANCE, {"__EVENTTARGET": "somethingElse"})
        assert result["sent"] is False and result["refused"]

    def test_a_post_anywhere_else(self):
        result = post("/Hilannetv2/Personal/SaveDetails.aspx", READ)
        assert result["sent"] is False and result["refused"]

    @pytest.mark.parametrize("path", [
        "/Hilannetv2/Attendance/calendarpage%2Easpx",
        "/Hilannetv2/Attendance/../Attendance/calendarpage.aspx",
        "/Hilannetv2//Attendance/calendarpage.aspx",
        "/HilanCenter/Public/api/LoginApi/LoginRequest/../ChangePassword",
        "/HilanCenter/Public/api/LoginApi/ChangePassword",
        "/hilannetv2/attendance/calendarpage.aspx",
    ], ids=["escaped", "dot-dot", "double-slash", "login-dot-dot", "other-login-api", "other-case"])
    def test_a_path_that_only_resembles_an_allowed_one(self, path):
        result = post(path, READ)
        assert result["sent"] is False and result["refused"]


class TestTheValuesAndTheQuery:
    def test_a_value_that_is_not_text(self):
        result = post(ATTENDANCE, {**READ, "__EVENTTARGET": 0})
        assert result["sent"] is False and "not text" in result["refused"]

    @pytest.mark.parametrize("missing", ["newPassword", "isChangePassword"])
    def test_an_undefined_value_in_a_login(self, missing):
        # JSON has no undefined; null is the nearest, and is refused the same way.
        result = post(LOGIN, {"username": "x", "password": "y", missing: None})
        assert result["sent"] is False

    @pytest.mark.parametrize("path", [
        ATTENDANCE + "&ctl00$mp$RG$btnSave=Save", ATTENDANCE + "&__EVENTTARGET=x",
        ATTENDANCE + "&other=1", LOGIN + "?isChangePassword=true&newPassword=n",
        ATTENDANCE + "&__CALLBACKID=grid", ATTENDANCE + "&x=%u0062tnSave",
        ATTENDANCE + "&next=ctl00$mp$RG$BtnSave",
    ], ids=["save", "postback-target", "other", "login-query", "callback", "percent-u", "save-in-a-value"])
    def test_a_query_string_that_is_not_a_read(self, path):
        body = READ if path.startswith(ATTENDANCE) else {"username": "x", "password": "y"}
        result = post(path, body)
        assert result["sent"] is False and result["refused"]

    def test_a_save_button_named_in_a_value_whatever_its_case(self):
        result = post(ATTENDANCE, {**READ, "__EVENTARGUMENT": "ctl00$mp$RG$BtnSave"})
        assert result["sent"] is False and "btnSave" in result["refused"]

    def test_no_read_action_at_all(self):
        result = post(ATTENDANCE, {"__calendarSelectedDays": "9740"})
        assert result["sent"] is False and "read action" in result["refused"]


class TestTheLoginBody:
    @pytest.mark.parametrize("change", [{"isChangePassword": "true"}, {"newPassword": "something"},
                                        {"isChangePassword": "1"}])
    def test_a_login_that_changes_the_password(self, change):
        result = post(LOGIN, {"username": "x", "password": "y", "isChangePassword": "false",
                              "newPassword": "", **change})
        assert result["sent"] is False and "password" in result["refused"]

    @pytest.mark.parametrize("field", ["IsChangePassword", "ischangepassword", "NewPassword", "Password"])
    def test_a_field_the_login_never_sends(self, field):
        result = post(LOGIN, {"username": "x", "password": "y", field: "true"})
        assert result["sent"] is False and field in result["refused"]

    def test_the_login_with_a_verification_code(self):
        result = post(LOGIN, {"username": "x", "password": "y", "verificationCode": "1", "saveBrowser": "true"})
        assert result == {"sent": True, "refused": None}

    def test_the_login_the_widget_sends(self):
        result = post(LOGIN, {"orgId": "", "username": "x", "password": "y", "isChangePassword": "false",
                              "newPassword": "", "id": "", "isEn": "false"})
        assert result == {"sent": True, "refused": None}


class TestTheAttendanceBody:
    """Only fields the month read itself sends, as client.py's READ_FIELDS."""

    def test_the_whole_read_the_widget_sends(self):
        body = {"__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "", "Time": "9",
                "DisableTimeout": "true", "__VIEWSTATE": "v", "__VIEWSTATEGENERATOR": "g",
                "H-XSRF-Token": "", "ctl00$mp$Strip$hCurrentItemId": "1",
                "ctl00$mp$currentMonth": "01/09/2026", "__EVENTVALIDATION": "e", **READ}
        assert post(ATTENDANCE, body) == {"sent": True, "refused": None}

    def test_a_field_the_read_does_not_send(self):
        result = post(ATTENDANCE, {**READ, "ctl00$mp$RG_Days_1_2026_09$ManualEntry": "08:00"})
        assert result["sent"] is False and "ManualEntry" in result["refused"]

    def test_a_named_postback_target(self):
        result = post(ATTENDANCE, {**READ, "__EVENTTARGET": "ctl00$mp$Strip$btnSomething"})
        assert result["sent"] is False and result["refused"]

    def test_two_read_actions_at_once(self):
        result = post(ATTENDANCE, {**READ, "ctl00$mp$RefreshPeriod": "x"})
        assert result["sent"] is False and result["refused"]

    @pytest.mark.parametrize("action", ["RefreshPeriod", "RefreshErrorsDays"])
    def test_the_other_read_actions(self, action):
        result = post(ATTENDANCE, {"__calendarSelectedDays": "9740", f"ctl00$mp${action}": "x"})
        assert result == {"sent": True, "refused": None}
