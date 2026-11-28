"""The web UI's doors, exercised through the real ASGI app.

tests/test_webapp_auth.py covers the verification logic; this covers the
wiring -- that a signed-out visitor cannot reach a page, that the session
cookie is set with the attributes it needs, and that the login page cannot be
made to say something a crafted link chose.
"""
import time

import pytest
from fastapi.testclient import TestClient

from bot.webapp import app as webapp
from bot.webapp import auth

TOKEN = "123456:test-token"
ADMIN_ID = 111


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth.config, "BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(auth.config, "ADMIN_IDS", (ADMIN_ID,), raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("BOT_USERNAME", "cu_statistics_bot")

    async def admin_ids():
        return set()

    async def version():
        return 7

    async def load():
        return {"SELECT_WEEK": ["Обери тиждень:"]}

    monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
    monkeypatch.setattr(webapp.configstore, "version", version)
    monkeypatch.setattr(webapp.configstore, "load", load)


@pytest.fixture
def client():
    # The app only ever sees HTTPS in production, and Secure cookies are
    # dropped over plain http, so the test client has to speak https too.
    with TestClient(webapp.app, base_url="https://testserver") as c:
        yield c


def payload(**overrides) -> dict[str, str]:
    params = {
        "id": str(ADMIN_ID),
        "first_name": "Ярослав",
        "username": "yaros",
        "auth_date": str(int(time.time())),
    }
    params.update({k: str(v) for k, v in overrides.items()})
    params["hash"] = auth.sign_widget_payload(params, TOKEN)
    return params


class TestTheDoorIsShut:
    def test_the_index_redirects_a_stranger_to_the_login_page(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/login"

    def test_a_forged_cookie_does_not_open_it(self, client):
        client.cookies.set(auth.SESSION_COOKIE, "not-a-real-cookie")
        assert client.get("/", follow_redirects=False).status_code == 307

    def test_the_login_page_is_reachable_signed_out(self, client):
        response = client.get("/login")
        assert response.status_code == 200
        assert "telegram-widget.js" in response.text

    def test_the_widget_cannot_render_without_a_bot_username(self, client, monkeypatch):
        monkeypatch.delenv("BOT_USERNAME", raising=False)
        response = client.get("/login")
        assert "telegram-widget.js" not in response.text
        assert "BOT_USERNAME" in response.text


class TestLogin:
    def test_a_genuine_login_sets_a_session_and_lands_on_the_index(self, client):
        response = client.get(
            "/auth/telegram", params=payload(), follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie.replace("samesite", "SameSite")

        assert "Ярослав" in client.get("/").text

    def test_a_forged_login_is_turned_away_without_a_cookie(self, client):
        params = payload()
        params["hash"] = "0" * 64
        response = client.get(
            "/auth/telegram", params=params, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login?error=bad_signature"
        assert "set-cookie" not in response.headers

    def test_a_non_admin_is_turned_away(self, client):
        response = client.get(
            "/auth/telegram", params=payload(id=999), follow_redirects=False
        )
        assert response.headers["location"] == "/login?error=not_admin"
        assert "set-cookie" not in response.headers

    def test_logging_out_clears_the_cookie(self, client):
        client.get("/auth/telegram", params=payload(), follow_redirects=False)
        response = client.get("/logout", follow_redirects=False)
        assert response.headers["location"] == "/login"
        assert 'custat_session=""' in response.headers["set-cookie"]

    def test_an_already_signed_in_admin_skips_the_login_page(self, client):
        client.get("/auth/telegram", params=payload(), follow_redirects=False)
        response = client.get("/login", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


class TestTheLoginPageCannotBeMadeToSpeak:
    def test_a_known_code_shows_our_wording(self, client):
        # Without the apostrophe: Jinja escapes it to &#39;, which is the
        # point -- the page is escaped output, not raw text.
        assert "signature did not check out" in client.get(
            "/login?error=bad_signature"
        ).text

    def test_an_arbitrary_message_is_not_reflected(self, client):
        """Otherwise a crafted link puts any sentence on a page that looks like
        ours -- 'введіть пароль тут' being the obvious one."""
        crafted = "Enter your password below"
        response = client.get("/login", params={"error": crafted})
        assert crafted not in response.text
        assert webapp.GENERIC_LOGIN_ERROR in response.text

    def test_no_error_shows_no_error(self, client):
        assert webapp.GENERIC_LOGIN_ERROR not in client.get("/login").text


class TestHeadersAndHealth:
    def test_security_headers_are_present(self, client):
        headers = client.get("/login").headers
        assert "telegram.org" in headers["content-security-policy"]
        assert "'unsafe-inline'" not in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"

    def test_health_is_open_but_says_nothing_sensitive(self, client):
        body = client.get("/healthz").json()
        assert body == {"ok": True, "config_version": 7, "keys": 1}

    def test_health_reports_a_database_it_cannot_reach(self, client, monkeypatch):
        async def boom():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(webapp.configstore, "version", boom)
        response = client.get("/healthz")
        assert response.status_code == 503
        assert response.json() == {"ok": False}

    def test_there_is_no_schema_endpoint(self, client):
        """FastAPI serves /openapi.json even with docs disabled, and it
        publishes the whole route map."""
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404


class TestEveryRefusalHasWording:
    def test_no_code_falls_through_to_the_generic_message(self):
        """A code with no entry in LOGIN_ERRORS shows the generic message
        forever, and nobody notices because the page still looks right."""
        import re
        from pathlib import Path

        source = Path(auth.__file__).read_text(encoding="utf-8")
        codes = set(re.findall(r'AuthError\([^)]*?,\s*"([a-z_]+)"', source, re.S))
        assert codes, "the scan found no codes, so it is not guarding anything"

        undescribed = codes - set(webapp.LOGIN_ERRORS)
        assert not undescribed, (
            f"{sorted(undescribed)} are raised by auth.py but have no wording in "
            "webapp.LOGIN_ERRORS. Add a message for each."
        )

    def test_no_wording_is_left_for_a_code_that_is_gone(self):
        import re
        from pathlib import Path

        source = Path(auth.__file__).read_text(encoding="utf-8")
        codes = set(re.findall(r'AuthError\([^)]*?,\s*"([a-z_]+)"', source, re.S))
        stale = set(webapp.LOGIN_ERRORS) - codes
        assert not stale, f"{sorted(stale)} is wording for a code nothing raises"
