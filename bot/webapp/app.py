"""The configuration web UI.

It renders what the bot will say, so the configuration reads as a script
rather than as a flat list of spreadsheet rows.

It runs in its own container and shares only the database with the bot, so a
fault here cannot take the bot offline. Nothing here writes to Telegram and
nothing here touches the data spreadsheet; the only thing it changes is the
configuration, and the bot notices within POLL_SECONDS.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot import registry
from bot.db import configstore
from bot.utils.maths import number_to_excel_column
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


# ----------------------------------------------------------- configuration --


def _field_or_404(key: str) -> registry.Field:
    field = registry.BY_KEY.get(key)
    if field is None:
        raise HTTPException(status_code=404, detail=f"No such configuration key: {key}")
    return field


async def _live() -> dict[str, list[str]]:
    """The configuration as stored. Read fresh: two people may be editing."""
    return await configstore.load()


def _button_labels(live: dict[str, list[str]]) -> dict[str, list[str]]:
    """Every label the bot matches incoming messages against, by key.

    Passed to validation so saving a label that already belongs to another
    button is refused rather than quietly shadowing it.
    """
    return {
        f.key: live.get(f.key, [])
        for f in registry.FIELDS
        if f.kind is registry.Kind.BUTTON and f.group is not registry.Group.UNUSED
    }


def pattern_preview(
    pattern: str, start_date: str, end_date: str
) -> list[dict] | None:
    """Week by week: how many questions, and which spreadsheet columns.

    The columns are the point. QUESTION_PATTERN decides where a week's answers
    land, so a change relocates every week after it -- the one thing about this
    field that is impossible to see from the digits alone.
    """
    if not pattern.isdigit() or not pattern:
        return None
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
    except (TypeError, ValueError):
        return None
    if start.year != end.year or end < start:
        return None

    start_week, end_week = start.isocalendar()[1], end.isocalendar()[1]
    rows = []
    column = 3  # the same starting column week_to_indices accumulates from
    for offset, week in enumerate(range(start_week, end_week + 1)):
        count = int(pattern[offset % len(pattern)])
        first = number_to_excel_column(column)
        last = number_to_excel_column(column + count - 1) if count else None
        rows.append(
            {
                "week": week,
                "questions": count,
                "columns": f"{first}–{last}" if count > 1 else (first if count else "—"),
            }
        )
        column += count
    return rows


def _entry(field: registry.Field, values: list[str]) -> dict:
    """One key as a page shows it.

    The list is called "phrasings", not "values": Jinja resolves
    ``entry.values`` to the dict's own .values method and silently iterates
    that instead, which renders nothing and raises only where it is used.
    """
    return {
        "field": field,
        "phrasings": values,
        "missing": not values,
        "label": registry.BY_KEY[field.key].label_uk,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, admin: SignedIn, saved: str | None = None):
    live = await _live()
    groups = []
    for group in registry.Group:
        if group is registry.Group.UNUSED:
            continue
        fields = [
            _entry(f, live.get(f.key, []))
            for f in registry.fields_in_group(group)
            # A question the schedule never asks is noise on this page.
            if not f.conditional or f.key in registry.question_keys(
                (live.get("QUESTION_PATTERN") or ["0"])[0]
            )
        ]
        if fields:
            groups.append(
                {
                    "group": group,
                    "label": registry.GROUP_LABELS_UK[group],
                    "help": registry.GROUP_HELP_UK.get(group, ""),
                    "fields": fields,
                }
            )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "admin": admin,
            "active": "config",
            "groups": groups,
            "saved": saved,
            "config_version": await configstore.version(),
            "source_is_database": bool(live),
        },
    )


def _draft_values(field: registry.Field, raw: list[str]) -> list[str]:
    """Form input turned into what would be stored.

    Blank boxes are dropped rather than stored: that is how a phrasing is
    removed, since there is no delete button per box and nothing here needs
    JavaScript. CRLF is normalised because a textarea submits it and the bot
    would send it.
    """
    values = [v.replace("\r\n", "\n").strip() for v in raw]
    return [v for v in values if v]


async def _edit_page(
    request: Request,
    admin: Admin,
    field: registry.Field,
    drafts: list[str],
    problems: list[validation.Problem] | None = None,
) -> HTMLResponse:
    live = await _live()
    # Always one empty box at the end, so adding a phrasing needs no button
    # press and removing one is just clearing it.
    boxes = [*drafts, ""] if field.variants else (drafts or [""])[:1]
    return templates.TemplateResponse(
        request,
        "edit.html",
        {
            "admin": admin,
            "active": "config",
            "field": field,
            "boxes": boxes,
            "stored": live.get(field.key, []),
            "problems": problems or [],
            "csrf": issue_csrf(admin),
            "group_label": registry.GROUP_LABELS_UK[field.group],
            "preview": _preview_for(field, drafts, live),
            "Kind": registry.Kind,
        },
    )


def _preview_for(
    field: registry.Field, drafts: list[str], live: dict[str, list[str]]
) -> list[dict] | None:
    if field.kind is not registry.Kind.PATTERN:
        return None
    pattern = (drafts or live.get(field.key) or [""])[0]
    return pattern_preview(
        pattern,
        (live.get("START_DATE") or [""])[0],
        (live.get("END_DATE") or [""])[0],
    )


async def _validate(
    field: registry.Field, values: list[str], live: dict[str, list[str]]
) -> list[validation.Problem]:
    other_date = None
    if field.key == "START_DATE":
        other_date = (live.get("END_DATE") or [None])[0]
    elif field.key == "END_DATE":
        other_date = (live.get("START_DATE") or [None])[0]
    return validation.check_variants(
        field,
        values,
        other_labels=_button_labels(live),
        other_date=other_date,
    )


@app.get("/edit/{key}", response_class=HTMLResponse)
async def edit_form(request: Request, admin: SignedIn, key: str):
    field = _field_or_404(key)
    return await _edit_page(request, admin, field, (await _live()).get(key, []))


@app.post("/edit/{key}", response_class=HTMLResponse)
async def edit_review(
    request: Request,
    admin: SignedIn,
    key: str,
    value: Annotated[list[str], Form()] = [],  # noqa: B006 — FastAPI reads the default
    csrf: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
):
    """Check the draft and show what will change. Nothing is written here."""
    field = _field_or_404(key)
    if not csrf_is_valid(csrf, admin):
        raise HTTPException(status_code=400, detail=STALE_FORM)

    drafts = _draft_values(field, value)
    if back:
        # The review step's way back, carrying the draft so it is not retyped.
        return await _edit_page(request, admin, field, drafts)

    live = await _live()
    problems = await _validate(field, drafts, live)
    if validation.blocking(problems):
        return await _edit_page(request, admin, field, drafts, problems)

    stored = live.get(key, [])
    if drafts == stored:
        return RedirectResponse(f"/#{key}", status_code=303)

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "admin": admin,
            "active": "config",
            "field": field,
            "drafts": drafts,
            "stored": stored,
            "problems": problems,
            "csrf": issue_csrf(admin),
            "group_label": registry.GROUP_LABELS_UK[field.group],
            "preview": _preview_for(field, drafts, live),
            "was_preview": _preview_for(field, stored, live),
            "Kind": registry.Kind,
        },
    )


@app.post("/save/{key}")
async def save(
    request: Request,
    admin: SignedIn,
    key: str,
    value: Annotated[list[str], Form()] = [],  # noqa: B006
    csrf: Annotated[str, Form()] = "",
):
    """Write the change. Re-validated, because the review step is a round trip."""
    field = _field_or_404(key)
    if not csrf_is_valid(csrf, admin):
        raise HTTPException(status_code=400, detail=STALE_FORM)

    live = await _live()
    drafts = _draft_values(field, value)
    problems = await _validate(field, drafts, live)
    if validation.blocking(problems):
        # Not reachable through the UI; reachable from a stale tab or a
        # hand-made request, and refusing is the only safe answer.
        logger.warning(f"refused a save of {key}: {problems[0].message}")
        return await _edit_page(request, admin, field, drafts, problems)

    await configstore.set_variants(key, drafts, admin.audit_name)
    logger.info(f"{admin.audit_name} saved {key}")
    return RedirectResponse(f"/?saved={key}#{key}", status_code=303)


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request, admin: SignedIn):
    """Who changed what. The answer to "the bot started saying something odd"."""
    entries = []
    for row in await configstore.audit(limit=200):
        field = registry.BY_KEY.get(row.key)
        entries.append(
            {
                "key": row.key,
                "label": field.label_uk if field else row.key,
                "changed_by": row.changed_by,
                "changed_at": row.changed_at,
                "old": _as_values(row.old_value),
                "new": _as_values(row.new_value),
            }
        )
    return templates.TemplateResponse(
        request,
        "history.html",
        {"admin": admin, "active": "history", "entries": entries},
    )


def _as_values(raw: str | None) -> list[str]:
    """An audit value back into a list.

    Stored as JSON because an ordered set of phrasings is the only honest
    representation; a batch import records an object instead, which is shown
    as-is rather than pretended to be phrasings.
    """
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return [raw]
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return [json.dumps(parsed, ensure_ascii=False)]


@app.get("/export.json")
async def export(admin: SignedIn):
    """The whole configuration, as a file.

    The database is backed up with the rest of Postgres, but a one-click copy
    before a risky edit is worth more than a backup you have to ask for.
    """
    return JSONResponse(
        await _live(),
        headers={
            "Content-Disposition": 'attachment; filename="custat-config.json"'
        },
    )
