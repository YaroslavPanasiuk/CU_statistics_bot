"""Telegram Login Widget verification and session cookies.

The widget hands back a signed payload. Verification follows Telegram's rule:
build a newline-joined ``key=value`` string of every field except ``hash``, in
alphabetical order, and compare an HMAC-SHA256 of it -- keyed by the SHA256 of
the bot token -- against the ``hash`` field.

Three checks matter, and skipping any one of them makes the whole thing
decorative:

* the signature, or anyone could request ``?id=<an admin's id>`` and be let in;
* ``auth_date`` freshness, or a captured login URL works forever;
* membership of the admin list, or any Telegram user with a valid signature is
  an editor.

Sessions are a signed, timestamped cookie rather than server state, so the
container can restart without logging everyone out. The cookie carries only the
numeric id, which is re-checked against the admin list on every request --
revoking access locks someone out immediately rather than when their cookie
happens to expire.

This is also where an invitation is claimed. A bot cannot turn @someone into a
numeric id; the Bot API has no call for it, deliberately. The login payload is
the only place the two arrive together, so a username invited from the web UI
becomes a real admin here, on their first sign-in, and the invitation is
consumed.
"""

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from bot.config import config
from bot.db import configstore

logger = logging.getLogger(__name__)

SESSION_COOKIE = "custat_session"

#: How long a login stays valid before the widget has to be used again.
SESSION_MAX_AGE = 12 * 60 * 60

#: How old the widget's payload may be. The redirect is immediate, so this only
#: needs to cover a slow hop; a captured URL stops working quickly.
AUTH_DATE_MAX_AGE = 300

#: Lifetime of a form token. Long enough to compose a message, short enough
#: that a token left in an abandoned tab stops working.
CSRF_MAX_AGE = 2 * 60 * 60

#: Exactly the fields the widget sends. Anything else in the query string is
#: ignored rather than folded into the check string, so an attacker cannot pad
#: the payload with a field Telegram never signed.
WIDGET_FIELDS = (
    "auth_date",
    "first_name",
    "id",
    "last_name",
    "photo_url",
    "username",
)


class AuthError(Exception):
    """Login could not be verified.

    Carries a ``code`` rather than text to show. The login page owns the
    wording, so nothing a caller supplies can end up rendered: reflecting the
    message through ``/login?error=...`` would let anyone put an arbitrary
    sentence -- "enter your password here" -- on a page that looks like ours.
    The message itself is for the log.
    """

    def __init__(self, message: str, code: str = "unknown"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Admin:
    tg_id: int
    username: str
    first_name: str

    @property
    def display_name(self) -> str:
        return self.first_name or self.username or str(self.tg_id)

    @property
    def audit_name(self) -> str:
        """How this admin is recorded in config_audit.

        The id is included because a username can change hands, and a year from
        now the only unambiguous answer to "who made this edit" is the number.
        """
        return f"@{self.username}" if self.username else f"id:{self.tg_id}"


def env_admin_ids() -> frozenset[int]:
    """The ids from ADMIN_IDS in .env.

    These cannot be revoked from the web UI on purpose: they are the way back
    in if the admins table is emptied by mistake.
    """
    return frozenset(config.ADMIN_IDS)


async def all_admin_ids() -> frozenset[int]:
    return env_admin_ids() | frozenset(await configstore.admin_ids())


async def _is_admin(tg_id: int) -> bool:
    """Membership, including admins granted from the web UI.

    The granted list is read from the database on every call, which is what
    makes a revocation take effect on the revoked admin's next request rather
    than when their cookie expires.
    """
    if tg_id in env_admin_ids():
        return True
    return tg_id in await configstore.admin_ids()


def _session_secret() -> str:
    """Key for signing session cookies.

    Derived from the bot token when SESSION_SECRET is unset, so the app works
    without extra configuration. It is deliberately a separate value from the
    token itself: a leaked session key must not be usable against the Telegram
    API.
    """
    configured = os.getenv("SESSION_SECRET")
    if configured:
        return configured
    if not config.BOT_TOKEN:
        raise AuthError("neither SESSION_SECRET nor BOT_TOKEN is configured", "not_configured")
    return hmac.new(
        config.BOT_TOKEN.encode(), b"custat-config-session", hashlib.sha256
    ).hexdigest()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="custat-config-session")


def _csrf_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="custat-config-csrf")


def check_signature(params: dict[str, str], *, now: float | None = None) -> Admin:
    """Verify a widget payload. Says nothing about who is allowed in.

    Separated from the admin check so the two cannot be confused: this
    establishes *which Telegram account* is calling, and authorise() decides
    whether that account may edit anything.
    """
    if not config.BOT_TOKEN:
        raise AuthError("BOT_TOKEN is not configured", "not_configured")

    received_hash = params.get("hash")
    if not received_hash:
        raise AuthError("the payload carries no hash", "no_signature")

    pairs = [f"{name}={params[name]}" for name in WIDGET_FIELDS if params.get(name)]
    if not pairs:
        raise AuthError("the payload carries none of the widget fields", "empty")

    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    expected = hmac.new(secret_key, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        logger.warning("rejected a login with a bad signature")
        raise AuthError("the hash does not match", "bad_signature")

    try:
        auth_date = int(params["auth_date"])
    except (KeyError, ValueError) as exc:
        raise AuthError("auth_date is missing or not a number", "bad_date") from exc

    age = (time.time() if now is None else now) - auth_date
    if age > AUTH_DATE_MAX_AGE:
        raise AuthError(f"auth_date is {age:.0f}s old", "stale")
    if age < -AUTH_DATE_MAX_AGE:
        raise AuthError(f"auth_date is {-age:.0f}s in the future", "clock")

    try:
        tg_id = int(params["id"])
    except (KeyError, ValueError) as exc:
        raise AuthError("id is missing or not a number", "bad_id") from exc

    return Admin(
        tg_id=tg_id,
        username=params.get("username", ""),
        first_name=params.get("first_name", ""),
    )


async def authorise(params: dict[str, str], *, now: float | None = None) -> Admin:
    """Verify a login and decide whether it may edit the configuration."""
    admin = check_signature(params, now=now)

    if not await _may_sign_in(admin):
        logger.warning(f"rejected a verified login from non-admin {admin.tg_id}")
        raise AuthError(f"{admin.tg_id} is not an admin", "not_admin")

    logger.info(f"admin {admin.tg_id} signed in")
    return admin


async def _may_sign_in(admin: Admin) -> bool:
    """Already an admin, or holding an invitation for this username."""
    if await _is_admin(admin.tg_id):
        return True
    if not admin.username:
        # Nothing to match an invitation against. Telegram allows accounts with
        # no username, and they simply cannot be invited by one.
        return False
    try:
        return await configstore.consume_invite(
            admin.username, admin.tg_id, admin.first_name
        )
    except Exception:
        # A database fault must read as "no", never as "yes".
        logger.exception(f"could not check the invitations for @{admin.username}")
        return False


def issue_session(admin: Admin) -> str:
    return _serializer().dumps(
        {"id": admin.tg_id, "u": admin.username, "n": admin.first_name}
    )


async def read_session(cookie: str | None) -> Admin | None:
    """Return the signed-in admin, or None.

    Admin membership is re-checked here rather than trusted from the cookie, so
    revoking someone's access takes effect on their next request.
    """
    if not cookie:
        return None
    try:
        data = _serializer().loads(cookie, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        return None
    except BadSignature:
        logger.warning("discarded a session cookie with an invalid signature")
        return None
    except AuthError:
        return None

    if not isinstance(data, dict):
        return None
    tg_id = data.get("id")
    if not isinstance(tg_id, int) or isinstance(tg_id, bool):
        return None
    if not await _is_admin(tg_id):
        return None
    return Admin(
        tg_id=tg_id,
        username=str(data.get("u", "")),
        first_name=str(data.get("n", "")),
    )


def issue_csrf(admin: Admin) -> str:
    """A token tying a form to the admin who was shown it.

    SameSite=Lax already stops a cross-site POST from carrying the session
    cookie, so this is a second lock on the same door -- cheap, and it means
    the write path does not depend on one cookie attribute being right.
    """
    return _csrf_serializer().dumps(admin.tg_id)


def csrf_is_valid(token: str | None, admin: Admin) -> bool:
    if not token:
        return False
    try:
        return _csrf_serializer().loads(token, max_age=CSRF_MAX_AGE) == admin.tg_id
    except (BadSignature, SignatureExpired, AuthError):
        return False


def sign_widget_payload(params: dict[str, str], bot_token: str) -> str:
    """Produce the hash Telegram would send. For tests and local development."""
    pairs = [f"{name}={params[name]}" for name in WIDGET_FIELDS if params.get(name)]
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    return hmac.new(secret_key, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
