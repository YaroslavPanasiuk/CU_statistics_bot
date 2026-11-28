"""Telegram Login Widget verification, sessions and CSRF.

Every test here is about the door. The three checks that matter are the
signature, the freshness of auth_date, and membership of the admin list;
skipping any one of them makes the other two decorative.
"""
import time

import pytest

from bot.webapp import auth
from bot.webapp.auth import Admin, AuthError

TOKEN = "123456:test-token"
ADMIN_ID = 111
STRANGER_ID = 222


@pytest.fixture(autouse=True)
def bot_token(monkeypatch):
    monkeypatch.setattr(auth.config, "BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(auth.config, "ADMIN_IDS", (ADMIN_ID,), raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)


@pytest.fixture
def no_granted_admins(monkeypatch):
    """No admins beyond .env, and no invitations."""
    async def admin_ids():
        return set()

    async def consume_invite(username, tg_id, first_name):
        return False

    monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
    monkeypatch.setattr(auth.configstore, "consume_invite", consume_invite)


def refused(code: str):
    """Assert the refusal carries a particular code.

    The code is the contract -- the login page maps it to wording, and the
    message itself is only ever logged.
    """
    import contextlib

    @contextlib.contextmanager
    def check():
        with pytest.raises(AuthError) as caught:
            yield
        assert caught.value.code == code, f"expected {code}, got {caught.value.code}"

    return check()


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


class TestSignature:
    def test_a_genuine_payload_is_accepted(self):
        admin = auth.check_signature(payload())
        assert admin.tg_id == ADMIN_ID
        assert admin.username == "yaros"

    def test_an_unsigned_payload_is_rejected(self):
        params = payload()
        del params["hash"]
        with refused("no_signature"):
            auth.check_signature(params)

    def test_a_forged_hash_is_rejected(self):
        params = payload()
        params["hash"] = "0" * 64
        with refused("bad_signature"):
            auth.check_signature(params)

    def test_changing_the_id_after_signing_is_rejected(self):
        """The whole point: otherwise anyone could request ?id=<an admin's id>."""
        params = payload()
        params["id"] = str(ADMIN_ID + 1)
        with refused("bad_signature"):
            auth.check_signature(params)

    def test_a_payload_signed_with_another_token_is_rejected(self):
        params = payload()
        params["hash"] = auth.sign_widget_payload(params, "999:someone-elses-token")
        with refused("bad_signature"):
            auth.check_signature(params)

    def test_an_extra_field_cannot_pad_the_check_string(self):
        """Only WIDGET_FIELDS are folded in, so a field Telegram never signed
        cannot be smuggled through -- nor can it invalidate a real login."""
        params = payload()
        params["is_admin"] = "true"
        assert auth.check_signature(params).tg_id == ADMIN_ID

    def test_no_bot_token_means_no_logins(self, monkeypatch):
        monkeypatch.setattr(auth.config, "BOT_TOKEN", None, raising=False)
        with refused("not_configured"):
            auth.check_signature(payload())


class TestFreshness:
    def test_a_stale_login_is_rejected(self):
        """Or a captured login URL works forever."""
        stale = int(time.time()) - auth.AUTH_DATE_MAX_AGE - 60
        with refused("stale"):
            auth.check_signature(payload(auth_date=stale))

    def test_a_login_just_inside_the_window_is_accepted(self):
        fresh = int(time.time()) - auth.AUTH_DATE_MAX_AGE + 30
        assert auth.check_signature(payload(auth_date=fresh)).tg_id == ADMIN_ID

    def test_a_login_from_the_future_is_rejected(self):
        """A clock this wrong would otherwise widen the window indefinitely."""
        ahead = int(time.time()) + auth.AUTH_DATE_MAX_AGE + 60
        with refused("clock"):
            auth.check_signature(payload(auth_date=ahead))

    def test_a_non_numeric_auth_date_is_rejected(self):
        params = {"id": str(ADMIN_ID), "first_name": "Я", "auth_date": "вчора"}
        params["hash"] = auth.sign_widget_payload(params, TOKEN)
        with refused("bad_date"):
            auth.check_signature(params)


class TestAuthorisation:
    async def test_an_env_admin_is_let_in(self, no_granted_admins):
        assert (await auth.authorise(payload())).tg_id == ADMIN_ID

    async def test_a_verified_stranger_is_turned_away(self, no_granted_admins):
        """A valid signature proves who you are, not that you may edit."""
        with refused("not_admin"):
            await auth.authorise(payload(id=STRANGER_ID))

    async def test_an_admin_granted_in_the_database_is_let_in(self, monkeypatch):
        async def admin_ids():
            return {STRANGER_ID}

        monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
        assert (await auth.authorise(payload(id=STRANGER_ID))).tg_id == STRANGER_ID

    async def test_an_invitation_is_claimed_at_sign_in(self, monkeypatch):
        """The login payload is the only place a username and an id arrive
        together, because the Bot API cannot resolve one to the other."""
        claimed = []

        async def admin_ids():
            return set()

        async def consume_invite(username, tg_id, first_name):
            claimed.append((username, tg_id, first_name))
            return True

        monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
        monkeypatch.setattr(auth.configstore, "consume_invite", consume_invite)

        admin = await auth.authorise(payload(id=STRANGER_ID, username="newcomer"))
        assert admin.tg_id == STRANGER_ID
        assert claimed == [("newcomer", STRANGER_ID, "Ярослав")]

    async def test_an_account_with_no_username_cannot_claim_an_invitation(
        self, monkeypatch
    ):
        """Telegram allows accounts without a username; there is nothing to
        match an invitation against, and guessing would be an open door."""
        asked = []

        async def admin_ids():
            return set()

        async def consume_invite(username, tg_id, first_name):
            asked.append(username)
            return True

        monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
        monkeypatch.setattr(auth.configstore, "consume_invite", consume_invite)

        params = {"id": str(STRANGER_ID), "first_name": "Хтось",
                  "auth_date": str(int(time.time()))}
        params["hash"] = auth.sign_widget_payload(params, TOKEN)
        with refused("not_admin"):
            await auth.authorise(params)
        assert asked == [], "the store should not be asked about an empty username"

    async def test_a_database_fault_reads_as_no(self, monkeypatch):
        """It must never fail open."""
        async def admin_ids():
            return set()

        async def consume_invite(username, tg_id, first_name):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
        monkeypatch.setattr(auth.configstore, "consume_invite", consume_invite)

        with refused("not_admin"):
            await auth.authorise(payload(id=STRANGER_ID))

    async def test_env_admins_cannot_be_locked_out_by_an_empty_table(
        self, no_granted_admins
    ):
        """ADMIN_IDS is the way back in if the admins table is emptied."""
        assert ADMIN_ID in await auth.all_admin_ids()


class TestSessions:
    async def test_a_session_round_trips(self, no_granted_admins):
        admin = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        restored = await auth.read_session(auth.issue_session(admin))
        assert restored == admin

    async def test_no_cookie_is_no_session(self, no_granted_admins):
        assert await auth.read_session(None) is None
        assert await auth.read_session("") is None

    async def test_a_tampered_cookie_is_discarded(self, no_granted_admins):
        cookie = auth.issue_session(
            Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        )
        assert await auth.read_session(cookie[:-4] + "AAAA") is None

    async def test_a_cookie_signed_with_another_secret_is_discarded(self, monkeypatch):
        """A different bot token must not yield a usable session."""
        cookie = auth.issue_session(
            Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        )
        monkeypatch.setattr(auth.config, "BOT_TOKEN", "999:other", raising=False)
        assert await auth.read_session(cookie) is None

    async def test_an_expired_cookie_is_discarded(self, monkeypatch, no_granted_admins):
        cookie = auth.issue_session(
            Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        )
        monkeypatch.setattr(auth, "SESSION_MAX_AGE", -1)
        assert await auth.read_session(cookie) is None

    async def test_membership_is_rechecked_not_trusted_from_the_cookie(
        self, monkeypatch
    ):
        """Revoking access has to lock someone out on their next request, not
        when their cookie happens to expire twelve hours later."""
        granted = {STRANGER_ID}

        async def admin_ids():
            return granted

        monkeypatch.setattr(auth.configstore, "admin_ids", admin_ids)
        cookie = auth.issue_session(
            Admin(tg_id=STRANGER_ID, username="newcomer", first_name="Хтось")
        )
        assert await auth.read_session(cookie) is not None

        granted.clear()
        assert await auth.read_session(cookie) is None

    async def test_the_session_secret_is_not_the_bot_token(self):
        """A leaked session key must not be usable against the Telegram API."""
        assert auth._session_secret() != TOKEN

    async def test_an_explicit_session_secret_is_honoured(self, monkeypatch):
        monkeypatch.setenv("SESSION_SECRET", "a-separate-secret")
        assert auth._session_secret() == "a-separate-secret"


class TestCsrf:
    def test_a_token_round_trips(self):
        admin = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        assert auth.csrf_is_valid(auth.issue_csrf(admin), admin)

    def test_a_missing_token_is_invalid(self):
        admin = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        assert not auth.csrf_is_valid(None, admin)
        assert not auth.csrf_is_valid("", admin)

    def test_another_admins_token_is_invalid(self):
        """A token is tied to the admin who was shown the form."""
        one = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        other = Admin(tg_id=STRANGER_ID, username="newcomer", first_name="Хтось")
        assert not auth.csrf_is_valid(auth.issue_csrf(other), one)

    def test_a_session_cookie_is_not_a_csrf_token(self):
        """Different salts, so one cannot be substituted for the other."""
        admin = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        assert not auth.csrf_is_valid(auth.issue_session(admin), admin)

    def test_an_expired_token_is_invalid(self, monkeypatch):
        admin = Admin(tg_id=ADMIN_ID, username="yaros", first_name="Ярослав")
        token = auth.issue_csrf(admin)
        monkeypatch.setattr(auth, "CSRF_MAX_AGE", -1)
        assert not auth.csrf_is_valid(token, admin)


class TestAuditName:
    def test_prefers_the_username(self):
        assert Admin(ADMIN_ID, "yaros", "Ярослав").audit_name == "@yaros"

    def test_falls_back_to_the_id(self):
        """An account with no username still has to be attributable."""
        assert Admin(ADMIN_ID, "", "Ярослав").audit_name == f"id:{ADMIN_ID}"
