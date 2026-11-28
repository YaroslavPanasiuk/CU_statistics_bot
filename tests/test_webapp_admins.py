"""Granting and revoking access from the web UI.

The dangerous paths here are the ones that look like they worked: revoking
yourself, revoking an id that comes from .env, or storing an invitation in a
form no login will ever match.
"""
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from bot.db import configstore
from bot.webapp import app as webapp
from bot.webapp import auth, validation

TOKEN = "123456:test-token"
ENV_ADMIN = 111
GRANTED_ADMIN = 222
STRANGER = 333


@dataclass
class FakeAdminRow:
    tg_id: int
    username: str
    first_name: str
    added_by: str
    added_at: datetime


@dataclass
class FakeInviteRow:
    username: str
    invited_by: str
    expires_at: datetime


class FakeStore:
    """Just enough of configstore to drive the pages, and a record of writes."""

    def __init__(self):
        self.admins_rows = [
            FakeAdminRow(GRANTED_ADMIN, "granted", "Олена", "@yaros", datetime.now())
        ]
        self.invite_rows: list[FakeInviteRow] = []
        self.calls: list[tuple] = []

    async def admins(self):
        return list(self.admins_rows)

    async def admin_ids(self):
        return {row.tg_id for row in self.admins_rows}

    async def pending_invites(self):
        return list(self.invite_rows)

    async def invite_admin(self, username, invited_by):
        self.calls.append(("invite", username, invited_by))
        self.invite_rows.append(
            FakeInviteRow(username, invited_by, datetime.now() + timedelta(days=7))
        )
        return username

    async def revoke_invite(self, username, revoked_by):
        self.calls.append(("revoke_invite", username, revoked_by))
        before = len(self.invite_rows)
        self.invite_rows = [r for r in self.invite_rows if r.username != username]
        return len(self.invite_rows) < before

    async def remove_admin(self, tg_id, removed_by):
        self.calls.append(("remove_admin", tg_id, removed_by))
        before = len(self.admins_rows)
        self.admins_rows = [r for r in self.admins_rows if r.tg_id != tg_id]
        return len(self.admins_rows) < before

    async def version(self):
        return 1

    async def load(self):
        return {}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in (
        "admins",
        "admin_ids",
        "pending_invites",
        "invite_admin",
        "revoke_invite",
        "remove_admin",
    ):
        monkeypatch.setattr(auth.configstore, name, getattr(fake, name))
        monkeypatch.setattr(webapp.configstore, name, getattr(fake, name))
    monkeypatch.setattr(webapp.configstore, "version", fake.version)
    monkeypatch.setattr(webapp.configstore, "load", fake.load)
    # The real one: normalisation is part of what these tests check.
    monkeypatch.setattr(
        webapp.configstore, "normalise_username", configstore.normalise_username
    )
    return fake


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth.config, "BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(auth.config, "ADMIN_IDS", (ENV_ADMIN,), raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("BOT_USERNAME", "cu_statistics_bot")


@pytest.fixture
def client(store):
    with TestClient(webapp.app, base_url="https://testserver") as c:
        yield c


def sign_in(client, tg_id=ENV_ADMIN, username="yaros"):
    params = {
        "id": str(tg_id),
        "first_name": "Ярослав",
        "username": username,
        "auth_date": str(int(time.time())),
    }
    params["hash"] = auth.sign_widget_payload(params, TOKEN)
    client.get("/auth/telegram", params=params, follow_redirects=False)


def csrf_of(client) -> str:
    """The token the page actually rendered, not one minted on the side."""
    page = client.get("/admins").text
    marker = 'name="csrf" value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


class TestNormalisation:
    """An invitation stored in a form no login will match is an invitation
    that silently never works."""

    @pytest.mark.parametrize(
        "pasted",
        [
            "olha_k",
            "@olha_k",
            "  @olha_k  ",
            "Olha_K",
            "t.me/olha_k",
            "https://t.me/olha_k",
            "http://t.me/olha_k",
            "telegram.me/olha_k",
            "https://t.me/@olha_k",
        ],
    )
    def test_every_way_of_pasting_a_handle_lands_on_the_same_value(self, pasted):
        assert configstore.normalise_username(pasted) == "olha_k"

    def test_an_empty_value_normalises_to_empty(self):
        assert configstore.normalise_username("  @ ") == ""


class TestUsernameChecks:
    def test_a_plausible_handle_passes(self):
        assert validation.check_username("olha_k") == []

    @pytest.mark.parametrize("handle", ["abc", "1olha", "olha-k", "o" * 33, "олена"])
    def test_an_impossible_handle_is_refused(self, handle):
        """Telegram would never issue these, so an invitation holding one can
        never be claimed."""
        assert validation.blocking(validation.check_username(handle))

    def test_an_empty_handle_asks_for_one(self):
        assert validation.blocking(validation.check_username(""))

    def test_an_existing_admin_is_refused(self):
        assert validation.blocking(
            validation.check_username("olha_k", already_admin=True)
        )

    def test_a_pending_invitation_is_refused(self):
        assert validation.blocking(
            validation.check_username("olha_k", already_invited=True)
        )


class TestThePageIsProtected:
    def test_signed_out_visitors_are_redirected(self, client):
        assert client.get("/admins", follow_redirects=False).status_code == 307

    def test_signed_out_visitors_cannot_invite(self, client, store):
        response = client.post(
            "/admins/invite", data={"username": "newcomer", "csrf": "x"},
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert store.calls == []

    def test_signed_out_visitors_cannot_revoke(self, client, store):
        response = client.post(
            "/admins/revoke", data={"tg_id": str(GRANTED_ADMIN), "csrf": "x"},
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert store.calls == []


class TestInviting:
    def test_a_handle_is_stored_normalised(self, client, store):
        sign_in(client)
        response = client.post(
            "/admins/invite",
            data={"username": "  @Olha_K  ", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admins?invited=olha_k"
        assert store.calls == [("invite", "olha_k", "@yaros")]

    def test_a_pasted_link_works(self, client, store):
        sign_in(client)
        client.post(
            "/admins/invite",
            data={"username": "https://t.me/olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        assert store.calls == [("invite", "olha_k", "@yaros")]

    def test_the_invitation_is_attributed_to_the_inviter(self, client, store):
        sign_in(client, username="")
        client.post(
            "/admins/invite",
            data={"username": "olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        # No username to record, so the id -- an edit must stay attributable.
        assert store.calls == [("invite", "olha_k", f"id:{ENV_ADMIN}")]

    def test_an_impossible_handle_is_refused_and_echoed_back(self, client, store):
        sign_in(client)
        response = client.post(
            "/admins/invite",
            data={"username": "олена", "csrf": csrf_of(client)},
        )
        assert response.status_code == 200
        assert "5–32" in response.text
        assert "олена" in response.text, "the typed value should survive the refusal"
        assert store.calls == []

    def test_inviting_an_existing_admin_is_refused(self, client, store):
        sign_in(client)
        response = client.post(
            "/admins/invite",
            data={"username": "granted", "csrf": csrf_of(client)},
        )
        assert "уже має доступ" in response.text
        assert store.calls == []

    def test_inviting_the_same_handle_twice_is_refused(self, client, store):
        sign_in(client)
        client.post(
            "/admins/invite",
            data={"username": "olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        store.calls.clear()
        response = client.post(
            "/admins/invite",
            data={"username": "@OLHA_K", "csrf": csrf_of(client)},
        )
        assert "уже запрошено" in response.text
        assert store.calls == []

    def test_a_missing_csrf_token_is_refused(self, client, store):
        sign_in(client)
        response = client.post("/admins/invite", data={"username": "olha_k"})
        assert response.status_code == 400
        assert store.calls == []

    def test_another_admins_csrf_token_is_refused(self, client, store):
        """A token is tied to the admin it was issued to."""
        other = auth.issue_csrf(
            auth.Admin(tg_id=STRANGER, username="someone", first_name="Хтось")
        )
        sign_in(client)
        response = client.post(
            "/admins/invite", data={"username": "olha_k", "csrf": other}
        )
        assert response.status_code == 400
        assert store.calls == []


class TestRevoking:
    def test_a_granted_admin_can_be_revoked(self, client, store):
        sign_in(client)
        response = client.post(
            "/admins/revoke",
            data={"tg_id": str(GRANTED_ADMIN), "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert store.calls == [("remove_admin", GRANTED_ADMIN, "@yaros")]

    def test_you_cannot_revoke_yourself(self, client, store):
        """It would lock the person out mid-session, and the only way back is
        another admin or the server."""
        sign_in(client, tg_id=GRANTED_ADMIN, username="granted")
        response = client.post(
            "/admins/revoke",
            data={"tg_id": str(GRANTED_ADMIN), "csrf": csrf_of(client)},
        )
        assert response.status_code == 200
        assert "у себе" in response.text
        assert store.calls == []

    def test_an_env_admin_cannot_be_revoked_here(self, client, store):
        """Pretending otherwise would show a revocation the next request undoes,
        because ADMIN_IDS is read from .env on every check."""
        sign_in(client, tg_id=GRANTED_ADMIN, username="granted")
        response = client.post(
            "/admins/revoke",
            data={"tg_id": str(ENV_ADMIN), "csrf": csrf_of(client)},
        )
        assert response.status_code == 200
        assert "ADMIN_IDS" in response.text
        assert store.calls == []

    def test_the_page_says_env_admins_are_not_revocable(self, client):
        sign_in(client)
        page = client.get("/admins").text
        assert "ADMIN_IDS" in page
        assert str(ENV_ADMIN) in page

    def test_an_unused_invitation_can_be_cancelled(self, client, store):
        sign_in(client)
        client.post(
            "/admins/invite",
            data={"username": "olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        store.calls.clear()
        response = client.post(
            "/admins/revoke",
            data={"username": "olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert store.calls == [("revoke_invite", "olha_k", "@yaros")]
        assert store.invite_rows == []

    def test_a_non_numeric_id_is_refused(self, client, store):
        sign_in(client)
        response = client.post(
            "/admins/revoke",
            data={"tg_id": "; drop table admins", "csrf": csrf_of(client)},
        )
        assert response.status_code == 400
        assert store.calls == []

    def test_a_missing_csrf_token_is_refused(self, client, store):
        sign_in(client)
        response = client.post("/admins/revoke", data={"tg_id": str(GRANTED_ADMIN)})
        assert response.status_code == 400
        assert store.calls == []


class TestWhatThePageShows:
    def test_granted_admins_are_listed_with_who_added_them(self, client):
        sign_in(client)
        page = client.get("/admins").text
        assert "@granted" in page
        assert "Олена" in page
        assert "@yaros" in page

    def test_your_own_row_is_marked_and_has_no_revoke_button(self, client):
        sign_in(client, tg_id=GRANTED_ADMIN, username="granted")
        page = client.get("/admins").text
        assert "це ви" in page
        assert f'name="tg_id" value="{GRANTED_ADMIN}"' not in page

    def test_a_pending_invitation_is_shown_with_its_expiry(self, client, store):
        sign_in(client)
        client.post(
            "/admins/invite",
            data={"username": "olha_k", "csrf": csrf_of(client)},
            follow_redirects=False,
        )
        page = client.get("/admins").text
        assert "@olha_k" in page
        assert "діє до" in page


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)
class TestTheWholeJourney:
    """Invite from the page, then sign in as the invited person.

    The fakes above cannot catch a mismatch between what the page stores and
    what the login compares against -- the one way this feature fails
    silently, because nobody can tell an unclaimable invitation from a person
    who has not got round to logging in yet.

    Driven through httpx rather than TestClient: TestClient runs the app in its
    own event loop, and the engine is a module-level object, so a fixture that
    touched the database in the test's loop and a request that touched it in
    the portal's would bind one engine to two loops.
    """

    @pytest.fixture
    async def real_store(self):
        from sqlalchemy import text as sql

        from bot.db import database

        await database.init_db()
        async with database.Session() as session:
            await session.execute(
                sql("TRUNCATE admins, admin_invites, config_audit, config_meta CASCADE")
            )
            await session.commit()
        yield
        await database.dispose_db()

    @pytest.fixture
    async def browser(self, real_store):
        """A signed-out client. One per person, so cookies do not mingle."""
        import httpx

        def make():
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=webapp.app),
                base_url="https://testserver",
            )

        opened = []

        async def open_one():
            client = make()
            opened.append(client)
            return client

        yield open_one
        for client in opened:
            await client.aclose()

    async def _sign_in(self, client, tg_id, username, first_name="Ярослав"):
        params = {
            "id": str(tg_id),
            "first_name": first_name,
            "username": username,
            "auth_date": str(int(time.time())),
        }
        params["hash"] = auth.sign_widget_payload(params, TOKEN)
        return await client.get("/auth/telegram", params=params)

    async def _csrf(self, client) -> str:
        page = (await client.get("/admins")).text
        marker = 'name="csrf" value="'
        start = page.index(marker) + len(marker)
        return page[start : page.index('"', start)]

    async def test_an_invited_person_can_sign_in_and_then_be_revoked(self, browser):
        mine = await browser()
        await self._sign_in(mine, ENV_ADMIN, "yaros")

        # Invited the way a person would actually type it.
        response = await mine.post(
            "/admins/invite",
            data={"username": "@Olha_K", "csrf": await self._csrf(mine)},
        )
        assert response.headers["location"] == "/admins?invited=olha_k"

        # Olha signs in. Telegram sends her handle in its own casing.
        hers = await browser()
        landed = await self._sign_in(hers, STRANGER, "Olha_K", "Олха")
        assert landed.status_code == 303, "the invitation was not claimed"
        assert landed.headers["location"] == "/"
        assert "Олха" in (await hers.get("/")).text

        # The invitation is spent, and she is a real admin now.
        page = (await mine.get("/admins")).text
        assert "діє до" not in page, "the invitation should have been consumed"
        assert "@olha_k" in page

        # Revoking her locks her out on her next request, not in twelve hours.
        await mine.post(
            "/admins/revoke",
            data={"tg_id": str(STRANGER), "csrf": await self._csrf(mine)},
        )
        assert (await hers.get("/")).status_code == 307

    async def test_a_second_login_cannot_reuse_a_spent_invitation(self, browser):
        """Two people, one handle: the second must not inherit the access."""
        mine = await browser()
        await self._sign_in(mine, ENV_ADMIN, "yaros")
        await mine.post(
            "/admins/invite",
            data={"username": "olha_k", "csrf": await self._csrf(mine)},
        )

        first = await self._sign_in(await browser(), STRANGER, "olha_k", "Перша")
        assert first.headers["location"] == "/"

        second = await self._sign_in(await browser(), STRANGER + 1, "olha_k", "Друга")
        assert second.headers["location"] == "/login?error=not_admin"
