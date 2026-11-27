# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python3.13 -m venv .venv                      # server runs Python 3.13
.venv/bin/pip install -r requirements-dev.txt # includes requirements.txt
.venv/bin/python -m bot.main                  # run (module form, from the repo root)
.venv/bin/python -m pytest -q                 # tests
.venv/bin/python -m pytest tests/test_maths.py::TestWeekToIndices -q   # single class
.venv/bin/ruff check .                        # lint (--fix to autofix)
```

Dependencies are pinned to exactly what production runs; `requirements-dev.txt`
adds pytest and ruff. `bot/utils/formatters.py` loads `bot/res/Bible.xml` by a
CWD-relative path, so the bot only works when launched from the repo root.

**Deployment**: a systemd unit `CU_statistics_bot.service` on the `lvivhp` host
(`/home/yaros/Projects/CU_statistics_bot`, venv at `venv/`, `Restart=always`),
with Postgres local to the same box. Deploys are `git pull` + `systemctl restart`.

Config comes from `.env` (see `.env.example`): `BOT_TOKEN`, `ADMIN_IDS`
(comma-separated), `DATABASE_URL` (async Postgres DSN, e.g.
`postgresql+asyncpg://...`), `GOOGLE_SHEET_CONFIG_URL`, `GOOGLE_SHEET_URL`,
`GOOGLE_CREDS_JSON` (inline service-account JSON), and `TZ`.

## What this is

An aiogram 3 Telegram bot that collects weekly activity statistics from
volunteers, stores them in Postgres, and keeps them in sync with a Google
Sheet. User-facing text is Ukrainian.

## Architecture

See README.md for setup and deployment. This section covers what needs
reading several files to work out.

**Three sources of truth, kept in sync:**

1. **Config spreadsheet** (`GOOGLE_SHEET_CONFIG_URL`) — a key/value sheet loaded
   into the `Lexicon` class by `Lexicon.load_from_sheet()`. Keys are slugified
   (`\W+` → `_`, uppercased) and set as **class attributes at runtime**, so
   `Lexicon.START_DATE`, `END_DATE`, `QUESTION_PATTERN`, `WORKSHEET_NAME`,
   `QUESTION_N`, `QUESTION_N_NAME`, `REMINDER{1,2,3}` do not exist in source —
   grep for the string key, not the attribute. Values may hold `;;`-separated
   variants; `select_random_line(key)` picks one, and `LexiconFilter` matches
   any of them. `validate_config()` (bot/lexicon.py) refuses to start on a
   config that would fail confusingly later; `REQUIRED_KEYS` lists what is
   mandatory. `text(key, default)` / `fill_default(...)` are for strings that
   are overridable but **must not** be required — adding one of those keys to
   `REQUIRED_KEYS` would break existing deployments on restart.
2. **Data spreadsheet** (`GOOGLE_SHEET_URL`) — the roster (non-empty column B
   from row 3) plus the per-week answer grid. Imported at startup and nightly,
   written back per submission. The sheet is authoritative, **but blank cells
   are skipped**: imports go through `database.merge_user_stats`
   (`SET stats = statistics.stats || excluded.stats`) so a blank or
   half-filled sheet cannot erase submitted answers. The questionnaire uses
   `save_user_stats`, which replaces wholesale.
3. **Postgres** — `volunteers` (`full_name` unique, nullable unique `tg_id`)
   and `statistics` (JSONB `stats` keyed `answer_1..N`, owned by
   `volunteer_id` → `volunteers.id`, unique on `(volunteer_id, week)`).
   Managed by **Alembic**; `init_db()` runs `alembic upgrade head`. Never add
   `create_all()` back — that is how the model and the database drifted for
   months. `statistics_archive` is declared in the metadata but read by
   nothing; it exists so autogenerate does not propose dropping it.

`sync_volunteers()` inserts new roster names and **deletes volunteers absent
from the sheet**, so it refuses an empty roster, and `load_volunteer_list()`
raises rather than returning an empty list on a Sheets failure. `main.py`
catches that and continues with the stored roster, because `Restart=always`
would otherwise mean a restart loop.

**Week arithmetic (`bot/utils/maths.py`) is the core abstraction**, and the
most error-prone thing here — `tests/test_maths.py` pins it. Weeks are ISO
week numbers. `QUESTION_PATTERN` is a string of digits, one per week, cycling,
giving how many questions that week asks. `week_to_indices` accumulates the
pattern from column 3 to locate a week's answer columns and returns a
**0-based, end-exclusive** span; `week_to_column_coords` turns it into
**1-based, inclusive** A1 letters. Those two conventions agree only because of
the `+1`/`-1` adjustments inside, and the tests assert that the import slice
and the export range describe the same cells. Out-of-range weeks raise
`WeekOutOfRange`. Everything assumes a **single calendar year**;
`validate_config()` enforces it.

**Handler routing** (`bot/handlers/__init__.py`) — order is load-bearing,
since several routers end in a catch-all:

1. `admin.router` (`IsAdmin`, messages *and* callbacks) — first, so
   administration works outside the semester.
2. `common.unavailable_router` (`~BotAvailable`) — out-of-season replies.
   Before the volunteer routers, which is why neither fill handler repeats a
   date check.
3. `common.registered_router` (`IsRegistered`) — menu and questionnaire;
   non-command text falls through to a random Bible verse.
4. `common.router` (`IsNotRegistered`) — registration, then a "not registered"
   catch-all. It had no filter, so it used to answer registered users too.
5. `common.fallback_router` (`IsRegistered`) — unknown-command reply. Must stay
   last: a catch-all earlier would shadow every admin command, since admins
   are registered volunteers.

Within `registered_router`, anything that must work mid-questionnaire has to
be registered **above** `process_any_answer`, which matches any text in
`waiting_for_answer`. It excludes commands, so they reach `admin.router`
instead of being stored as answers — production has a row proving what
happened before. `tests/test_handler_order.py` guards all of this.

`VolunteerMiddleware` (outer, on `dp.update`) resolves the telegram user to a
volunteer row once per update and injects it as `volunteer`; filters and
handlers take that parameter instead of querying. Only
`process_registration_name` looks up for itself, since the middleware ran
before the row was bound.

Stats collection is an FSM (`StatisticsCollection`) storing `answer_N` in
state; the next index is the count of existing `answer_*` keys, and the flow
ends when that exceeds `questions_in_week(week)`. Answers must be
non-negative integers (`parse_answer`). `/fill_stats` targets the week of
`now - config.LAG_THRESHOLD_DAYS` so Sunday and Monday submissions land on the
week just ended.

**Scheduler** — `AsyncIOScheduler` in `main.py`, injected as `dp["scheduler"]`
(admin handlers receive it as a `scheduler` argument). Three escalating Sunday
reminders (17:00/19:00/21:00) go to volunteers whose **sheet row** is still
blank for the current week, plus a nightly re-import. One summary per run goes
to `config.ADMIN_IDS[0]`, which is why `ADMIN_IDS` is an ordered tuple.

## Conventions

- Imports are absolute from the `bot` package, so always run as a module from the repo root.
- **All gspread calls go through `asyncio.to_thread`**, and the worksheet is resolved *inside* the threaded helper — passing `data_worksheet()` as an argument to `to_thread` evaluates it on the event loop. `read_all_values` / `read_range` / `write_values` are the entry points; they retry 429 and 5xx with backoff.
- `logging`, never `print`. Levels matter: a swallowed Sheets error is a `logger.error`.
- User-facing text is HTML (`parse_mode="HTML"`) and escaped. Not MarkdownV2 — question names from the sheet routinely contain characters it would reject.
- Filter parameters must not share a name with a middleware data key: aiogram passes the event positionally *and* fills any matching parameter from data. See `IsAdmin.telegram_event`.
- Tests: `pytest -q` runs without any services; database tests skip unless `TEST_DATABASE_URL` is set.
