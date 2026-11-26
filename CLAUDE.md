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

**Three sources of truth, kept in sync:**

1. **Config spreadsheet** (`GOOGLE_SHEET_CONFIG_URL`) — a key/value sheet loaded
   into the `Lexicon` class at startup by `Lexicon.load_from_sheet()`. Keys are
   slugified (`\W+` → `_`, uppercased) and set as **class attributes at
   runtime**, so names like `Lexicon.START_DATE`, `Lexicon.END_DATE`,
   `Lexicon.QUESTION_PATTERN`, `Lexicon.WORKSHEET_NAME`, `Lexicon.QUESTION_N`,
   `Lexicon.QUESTION_N_NAME`, `Lexicon.REMINDER{1,2,3}` do not exist in source —
   grep for the string key instead of the attribute. Values may hold several
   variants separated by `;;`; `select_random_line(key)` picks one. Admins can
   reload with `/reload_config`.
2. **Data spreadsheet** (`GOOGLE_SHEET_URL`) — the volunteer roster (column B
   from row 3, last row excluded) plus the per-week answer grid. Imported at
   startup and nightly at 00:00 (`import_stats_from_sheet`), and written back
   per-submission (`export_stats_to_sheet`).
3. **Postgres** — `volunteers` (`full_name` unique, nullable unique `tg_id`) and
   `statistics` (JSONB `stats` keyed `answer_1..N`, unique on `(tg_id, week)`,
   upserted). Tables are created with `create_all`; there are no migrations.

`sync_volunteers()` at startup inserts new roster names and **deletes
volunteers absent from the sheet** (cascading their statistics). The sheet is
authoritative for who exists.

**Week arithmetic (`bot/utils/maths.py`) is the core abstraction.** Weeks are
ISO week numbers. `Lexicon.QUESTION_PATTERN` is a string of digits — one digit
per week, cycling — giving how many questions that week asks. `week_to_indices`
accumulates that pattern from column 3 to locate a week's answer columns, and
`week_to_column_coords` turns them into A1 letters. Everything derives from
`Lexicon.START_DATE`/`END_DATE`; a change to the pattern or start date shifts
every column mapping. All of this assumes a single calendar year (no year
rollover).

**Handler routing** (`bot/handlers/__init__.py`) — order matters, since each
router ends in a catch-all:

- `common.registered_router` (filter `IsRegistered`) — menu, stats collection, `/get_my_stats`; falls through to a random Bible verse for any non-command text.
- `admin.router` (filter `IsAdmin`) — `/list_jobs`, `/remind_volunteers`, `/remind_admin`, `/broadcast`, `/register_user`, `/unregister_user`, `/reload_config`, `/reload_data`.
- `common.router` — unregistered users: name-picking registration, then a "not registered" catch-all.
- `common.unavailable_router` (filter `~BotAvailable`, i.e. outside the semester week range) exists but is **not registered** in `register_handlers`.

Stats collection is an FSM (`StatisticsCollection`) that stores answers in FSM
state as `answer_N`; the next index is inferred by counting existing
`answer_*` keys, and the flow finishes when that exceeds
`questions_in_week(week)`. `/fill_stats` targets the week of
`now - Config.LAG_TRESHOLD_DAYS` so Sunday/Monday submissions land on the week
just ended.

**Scheduler** — an `AsyncIOScheduler` created in `main.py` and injected as
`dp["scheduler"]` (admin handlers receive it as a `scheduler` argument). Three
escalating Sunday reminders (17:00/19:00/21:00, levels 1–3) go only to
volunteers whose row in the sheet is still blank for the current week
(`fetch_users_with_no_stats` reads the sheet, not the DB), plus a nightly
re-import. Each reminder also pings the first admin ID.

## Conventions

- Imports are absolute from the `bot` package (`from bot.utils...`), so always run as a module.
- Google Sheets and Telegram failures are swallowed and `print`ed rather than raised or logged; the bot is expected to keep running.
- `format_all_stats_for_user` emits `MarkdownV2`; reminders and admin replies use `parse_mode="HTML"`.
