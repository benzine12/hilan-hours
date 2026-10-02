# -*- coding: utf-8 -*-
"""Logging in the way the browser does.

The subdomain does not identify the organisation: the login page embeds an
initialData model carrying OrgId, and Hilan answers an empty one with the same
"wrong username or password" it gives for a bad password. Posting LoginRequest
without reading it first makes a correct password look wrong.
"""
import httpx
import pytest

from hilan import config
from hilan.client import HilanClient, LoginFailed
from hilan.config import Credentials

LOGIN_MODEL = (
    'var x = "...\\"initialData\\":{\\"IsMobileApp\\":false,\\"State\\":2,'
    '\\"OrgId\\":\\"1000\\",\\"IsShowOrganizationSelection\\":false,'
    '\\"IsShowId\\":false,\\"OrgName\\":\\"חברה לדוגמה\\"}...";'
)

CREDS = Credentials(username="12345", password="s3cret")


@pytest.fixture
def cookie_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(config, "COOKIE_FILE", path)
    monkeypatch.setattr("hilan.client.COOKIE_FILE", path)
    return path


def recorder(*, model_page=LOGIN_MODEL, ok=True):
    """Collects every request and answers a successful login."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/HilanCenter/Public/api/LoginApi/LoginRequest":
            if ok:
                return httpx.Response(200, json={"IsFail": False, "Code": 0})
            return httpx.Response(
                200,
                json={
                    "IsFail": True, "Code": 1,
                    "ErrorMessage": "הסיסמה או פרטי משתמש אינם נכונים",
                },
            )
        return httpx.Response(200, text=model_page)

    return httpx.MockTransport(handler), seen


def body_of(request) -> dict[str, str]:
    from urllib.parse import parse_qs

    # keep_blank_values, or the empty fields the browser also sends vanish.
    parsed = parse_qs(request.content.decode(), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items()}


class TestOrgIdComesFromTheLoginPage:
    def test_the_login_page_is_fetched_first(self, cookie_file):
        transport, seen = recorder()
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        assert seen[0].method == "GET"
        assert seen[0].url.path == "/login"

    def test_the_org_id_is_sent(self, cookie_file):
        transport, seen = recorder()
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        assert body_of(seen[-1])["orgId"] == "1000"

    def test_credentials_are_sent_verbatim(self, cookie_file):
        transport, seen = recorder()
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        sent = body_of(seen[-1])
        assert sent["username"] == "12345"
        assert sent["password"] == "s3cret"

    def test_the_browsers_other_fields_are_present(self, cookie_file):
        transport, seen = recorder()
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        sent = body_of(seen[-1])
        for field in ("isChangePassword", "id", "isEn"):
            assert field in sent, field

    def test_it_is_an_xhr_like_the_browsers(self, cookie_file):
        transport, seen = recorder()
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        assert seen[-1].headers.get("x-requested-with") == "XMLHttpRequest"

    def test_a_page_without_a_model_still_attempts_the_login(self, cookie_file):
        """Better a try with an empty orgId than refusing to log in at all."""
        transport, seen = recorder(model_page="<html>no model here</html>")
        with HilanClient(transport=transport) as c:
            c.login(CREDS)
        assert body_of(seen[-1])["orgId"] == ""

    def test_failure_still_raises(self, cookie_file):
        transport, _ = recorder(ok=False)
        with HilanClient(transport=transport) as c:
            with pytest.raises(LoginFailed, match="הסיסמה"):
                c.login(CREDS)


class TestOnlyAClearYesIsSuccess:
    """An answer that does not say IsFail: false is not taken for a login.

    Taking it for one would store the password and lift the stop on refused
    passwords; then every scheduled run sends it again, which is how an
    account gets locked out.
    """

    def answering(self, status=200, **answer):
        def handler(request):
            if request.url.path == "/HilanCenter/Public/api/LoginApi/LoginRequest":
                return httpx.Response(status, **answer)
            return httpx.Response(200, text=LOGIN_MODEL)
        return httpx.MockTransport(handler)

    @pytest.mark.parametrize("answer", [{}, {"Success": False}, {"IsFail": None},
                                        {"IsFail": "false"}, {"IsFail": 0}, []],
                             ids=["empty", "other-shape", "null", "string", "zero", "list"])
    def test_an_answer_of_another_shape_is_a_refusal(self, cookie_file, answer):
        from hilan.client import Rejected

        with HilanClient(transport=self.answering(json=answer)) as client:
            with pytest.raises(Rejected, match="does not recognise"):
                client.login(CREDS)

    def test_a_clear_yes_is_success(self, cookie_file):
        with HilanClient(transport=self.answering(json={"IsFail": False})) as client:
            client.login(CREDS)

    def test_a_server_error_is_not_a_refusal(self, cookie_file):
        from hilan.client import Rejected

        transport = self.answering(503, json={"IsFail": True})
        with HilanClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                client.login(CREDS)
        assert not issubclass(httpx.HTTPStatusError, Rejected)
