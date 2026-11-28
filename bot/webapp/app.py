"""The configuration web UI.

It renders what the bot will say, so the configuration reads as a script
rather than as a flat list of spreadsheet rows.

It runs in its own container and shares only the database with the bot, so a
fault here cannot take the bot offline. Nothing here writes to Telegram and
nothing here touches the data spreadsheet; the only thing it changes is the
configuration, and the bot notices within POLL_SECONDS.
"""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot.db import configstore
from bot.webapp import validation
from bot.webapp.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    Admin,
    AuthError,
    authorise,
    csrf_is_valid,
    env_admin_ids,
    issue_csrf,
    issue_session,
    read_session,
)

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

#: What the login page says for each AuthError code. The wording lives here,
#: never in the URL, so a crafted link cannot put words on a page that looks
#: like ours.
#:
#: These are in English while the rest of the page is Ukrainian, deliberately:
#: an error here is almost always something to fix on the server or in
#: BotFather, and the person reading it is whoever is being called to fix it.
LOGIN_ERRORS = {
    "no_signature": "Telegram's reply carried no signature. Try again.",
    "empty": "Telegram's reply was empty. Try again.",
    "bad_signature": "Telegram's signature did not check out.",
    "bad_date": "The sign-in timestamp was unreadable.",
    "stale": "That sign-in has expired. Use the Telegram button again.",
    "clock": "The sign-in is dated in the future — check the server clock.",
    "bad_id": "The Telegram account id was unreadable.",
    "not_admin": (
        "This account cannot edit the configuration. Ask an admin to add your "
        "Telegram username."
    ),
    "not_configured": "Sign-in is not configured on the server.",
}
GENERIC_LOGIN_ERROR = "Could not sign you in. Try again."

# No docs and no schema endpoint: /openapi.json is served by default even when
# docs_url is disabled, and it publishes the whole route map to anyone.
app = FastAPI(
    title="Статистика ЦУ — налаштування",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def bot_username() -> str:
    """The bot the login widget should use. Required for the widget to render."""
    return os.getenv("BOT_USERNAME", "")


async def current_admin(request: Request) -> Admin:
    admin = await read_session(request.cookies.get(SESSION_COOKIE))
    if admin is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return admin


#: The signed-in admin, or a redirect to the login page.
SignedIn = Annotated[Admin, Depends(current_admin)]


@app.exception_handler(HTTPException)
async def redirect_unauthenticated(request: Request, exc: HTTPException):
    """Send signed-out visitors to the login page instead of showing a 307 body."""
    if exc.status_code == 307 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=307)
    return JSONResponse(
        {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers the reverse proxy should not be relied on to add.

    The CSP allows Telegram's widget script and frame, which the login page
    needs, and nothing else -- no inline script, no other origins.
    """
    response: Response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://telegram.org; "
        "frame-src https://oauth.telegram.org https://telegram.org; "
        "img-src 'self' https://t.me https://telegram.org data:; "
        "style-src 'self'; base-uri 'none'; form-action 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/healthz")
async def healthz():
    """Liveness, plus whether the configuration has been imported yet.

    Deliberately unauthenticated and deliberately uninformative: a count and a
    version number, nothing about who the admins are or what the bot says.
    """
    try:
        return {
            "ok": True,
            "config_version": await configstore.version(),
            "keys": len(await configstore.load()),
        }
    except Exception as exc:
        logger.error(f"health check could not reach the database: {exc}")
        return JSONResponse({"ok": False}, status_code=503)


@app.get("/login", response_class=HTMLResponse)
async def login(request: Request, error: str | None = None):
    if await read_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/", status_code=303)
    message = None
    if error is not None:
        message = LOGIN_ERRORS.get(error, GENERIC_LOGIN_ERROR)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"bot_username": bot_username(), "error": message},
    )


@app.get("/auth/telegram")
async def auth_telegram(request: Request):
    """Where the Telegram Login Widget sends the signed payload."""
    try:
        admin = await authorise(dict(request.query_params))
    except AuthError as exc:
        logger.warning(f"login refused ({exc.code}): {exc}")
        return RedirectResponse(f"/login?error={exc.code}", status_code=303)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(admin),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        # The widget only works over HTTPS, so this costs nothing and stops the
        # cookie from ever being sent in clear if someone proxies the app badly.
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, admin: SignedIn):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"admin": admin, "config_version": await configstore.version()},
    )


# ------------------------------------------------------------------ admins --

STALE_FORM = "This form is out of date. Reload the page and try again."


async def _admins_page(
    request: Request,
    admin: Admin,
    problems: list[validation.Problem] | None = None,
    draft: str = "",
    invited: str | None = None,
) -> HTMLResponse:
    granted = await configstore.admins()
    return templates.TemplateResponse(
        request,
        "admins.html",
        {
            "admin": admin,
            "active": "admins",
            # Shown first and not revocable here: .env is the way back in if
            # this table is ever emptied by mistake.
            "from_env": [
                {"tg_id": tg_id, "is_you": tg_id == admin.tg_id}
                for tg_id in sorted(env_admin_ids())
            ],
            "granted": [
                {
                    "tg_id": row.tg_id,
                    "username": row.username,
                    "first_name": row.first_name,
                    "added_by": row.added_by,
                    "added_at": row.added_at,
                    "is_you": row.tg_id == admin.tg_id,
                }
                for row in granted
            ],
            "invites": await configstore.pending_invites(),
            "problems": problems or [],
            "draft": draft,
            "invited": invited,
            "csrf": issue_csrf(admin),
        },
    )


@app.get("/admins", response_class=HTMLResponse)
async def admins_page(request: Request, admin: SignedIn, invited: str | None = None):
    return await _admins_page(request, admin, invited=invited)


@app.post("/admins/invite", response_class=HTMLResponse)
async def invite_admin(
    request: Request,
    admin: SignedIn,
    username: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Invite a username. It becomes access on that person's first sign-in."""
    if not csrf_is_valid(csrf, admin):
        raise HTTPException(status_code=400, detail=STALE_FORM)

    handle = configstore.normalise_username(username)
    problems = validation.check_username(
        handle,
        already_admin=handle in {row.username for row in await configstore.admins()},
        already_invited=handle
        in {row.username for row in await configstore.pending_invites()},
    )
    if validation.blocking(problems):
        return await _admins_page(request, admin, problems, draft=username)

    await configstore.invite_admin(handle, admin.audit_name)
    logger.info(f"{admin.audit_name} invited @{handle}")
    return RedirectResponse(f"/admins?invited={handle}", status_code=303)


@app.post("/admins/revoke", response_class=HTMLResponse)
async def revoke_admin(
    request: Request,
    admin: SignedIn,
    tg_id: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
):
    """Take back access, or cancel an invitation nobody has used yet."""
    if not csrf_is_valid(csrf, admin):
        raise HTTPException(status_code=400, detail=STALE_FORM)

    if username:
        await configstore.revoke_invite(username, admin.audit_name)
        logger.info(f"{admin.audit_name} cancelled the invitation for @{username}")
        return RedirectResponse("/admins", status_code=303)

    try:
        target = int(tg_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="That is not a valid id.") from None

    if target == admin.tg_id:
        # Letting this through locks the person out mid-session, and the only
        # way back is another admin or the server.
        return await _admins_page(
            request,
            admin,
            [validation.error("You cannot revoke your own access.")],
        )

    if target in env_admin_ids():
        # Not ours to take: it comes from ADMIN_IDS. Pretending otherwise would
        # show a revocation that the very next request silently undoes.
        return await _admins_page(
            request,
            admin,
            [
                validation.error(
                    "This access comes from ADMIN_IDS in .env on the server, so it "
                    "cannot be taken away here. Remove the id from ADMIN_IDS and "
                    "restart the bot."
                )
            ],
        )

    await configstore.remove_admin(target, admin.audit_name)
    logger.info(f"{admin.audit_name} revoked access for {target}")
    return RedirectResponse("/admins", status_code=303)
