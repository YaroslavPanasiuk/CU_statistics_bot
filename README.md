# CU_statistics_bot

A Telegram bot that collects weekly activity statistics from volunteers,
stores them in Postgres, and keeps them in sync with a Google Sheet.
User-facing text is Ukrainian and is authored in a separate config
spreadsheet rather than in code.

## Requirements

- Python 3.13 (what production runs; the code needs 3.12+ for its f-strings)
- Postgres
- A Google service account with access to both spreadsheets

## Setup

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # includes requirements.txt
cp .env.example .env                            # then fill it in
.venv/bin/python -m bot.main
```

Run it as a module from the repo root: imports are absolute from the `bot`
package.

### Configuration

`.env` holds only secrets and connection details:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | Telegram bot token |
| `ADMIN_IDS` | Comma-separated Telegram user ids. The first receives scheduler reports. |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host/db` |
| `GOOGLE_SHEET_CONFIG_URL` | Spreadsheet holding the key/value config |
| `GOOGLE_SHEET_URL` | Spreadsheet holding the volunteer roster and answers |
| `GOOGLE_CREDS_JSON` | Service-account JSON, inline |
| `TZ` | Timezone for the cron schedules, e.g. `Europe/Kyiv` |

Everything else — all user-facing text, the semester dates, the question
schedule, the worksheet name — lives in the **config spreadsheet** as a
two-column key/value sheet. Keys are slugified (`\W+` → `_`, uppercased) and
set as attributes on `Lexicon` at runtime, so names like `START_DATE` and
`QUESTION_PATTERN` do not appear in the source. A value may hold several
variants separated by `;;`, and one is picked at random each time.

`validate_config()` runs at startup and on `/reload_config`, and refuses to
start on a config that would fail confusingly later — a missing required key,
a semester spanning New Year, a non-numeric `QUESTION_PATTERN`, or too few
`QUESTION_N` keys for the busiest week. See `REQUIRED_KEYS` in
`bot/lexicon.py` for the full list.

Some strings are optional: they have built-in Ukrainian fallbacks and can be
overridden from the sheet without being required (`GREETING`, `CANCELLED`,
`UNKNOWN_COMMAND`, and others — grep for `text(` in `bot/`).

## Tests

```bash
.venv/bin/python -m pytest -q          # unit tests, no services needed
.venv/bin/ruff check .                 # lint
```

Database integration tests are skipped unless you point them at a Postgres:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=cutest \
    -p 55432:5432 postgres:17
TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/cutest \
    .venv/bin/python -m pytest -q
```

On NixOS, `greenlet` needs `LD_LIBRARY_PATH` pointed at a `libstdc++.so.6`
for anything that touches the database.

## Schema

Managed by Alembic. `init_db()` runs `alembic upgrade head` at startup, so a
deploy is just `git pull` + restart and the schema can never drift from the
model again. `0001_baseline` deliberately describes the schema as it existed
in production — including a missing foreign key that `create_all()` never
applied — so existing databases are stamped at it (`alembic stamp 0001`)
while fresh ones run it for real.

`statistics` rows are owned by `volunteers.id`. `statistics_archive` holds
rows whose volunteer was deleted before the foreign key existed; nothing
reads it.

## Deployment

A systemd unit on the `lvivhp` host:

```
/etc/systemd/system/CU_statistics_bot.service
WorkingDirectory=/home/yaros/Projects/CU_statistics_bot
ExecStart=venv/bin/python -m bot.main
Restart=always, RestartSec=5
```

Postgres runs on the same box. Deploying:

```bash
ssh lvivhp
cd ~/Projects/CU_statistics_bot
pg_dump "$DATABASE_URL" > ~/backup_$(date +%F).sql   # before any migration
git pull
venv/bin/pip install -r requirements.txt             # when pins change
sudo systemctl restart CU_statistics_bot
journalctl -u CU_statistics_bot -f
```

The installed unit runs as **root**, which the bot does not need.
`deploy/CU_statistics_bot.service` is a hardened replacement with a dedicated
user and the usual `Protect*` restrictions; adopting it is a manual step
documented in that file.

## Known limitations

- **One calendar year per semester.** All week arithmetic uses bare ISO week
  numbers, so a semester from September to January cannot be represented.
  `validate_config()` rejects such a config rather than misbehaving.
- **FSM state is in memory.** A restart loses any half-finished
  questionnaire; the volunteer has to start the week again. Switching to Redis
  storage would fix it.
- **The sheet is authoritative.** The nightly import overwrites the database
  from the sheet, but blank cells are skipped, so a blank or half-filled sheet
  cannot erase submitted answers.
- **Registration is open.** Anyone who can reach the bot and knows a
  volunteer's name as it appears on the roster can register as them. The
  roster itself is no longer disclosed.

## Admin commands

`/list_jobs`, `/remind_volunteers [level]`, `/remind_admin [level]`,
`/broadcast [message]` (asks for confirmation), `/register_user [name],[tg_id]`,
`/unregister_user [tg_id|name]`, `/reload_config`, `/reload_data`.
