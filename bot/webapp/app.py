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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot.db import configstore
from bot.webapp.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    Admin,
    AuthError,
    authorise,
    issue_session,
    read_session,
)

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

#: What the login page says for each AuthError code. The wording lives here,
#: never in the URL, so a crafted link cannot put words on a page that looks
#: like ours.
LOGIN_ERRORS = {
    "no_signature": "Відповідь Telegram не містить підпису. Спробуйте ще раз.",
    "empty": "Відповідь Telegram порожня. Спробуйте ще раз.",
    "bad_signature": "Підпис Telegram не підтверджено.",
    "bad_date": "Некоректна дата входу.",
    "stale": "Термін дії входу вичерпано. Спробуйте ще раз.",
    "clock": "Дата входу в майбутньому — перевірте годинник.",
    "bad_id": "Некоректний ідентифікатор користувача.",
    "not_admin": (
        "Цей акаунт не має доступу до налаштувань. Попросіть адміністратора "
        "додати ваш нік у Telegram."
    ),
    "not_configured": "Вхід не налаштовано на сервері.",
}
GENERIC_LOGIN_ERROR = "Не вдалося увійти. Спробуйте ще раз."

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
