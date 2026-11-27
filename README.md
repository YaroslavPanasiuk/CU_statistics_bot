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

A systemd unit on the `lvivhp` host, `WorkingDirectory=/home/yaros/Projects/CU_statistics_bot`,
`ExecStart=venv/bin/python -m bot.main`, `Restart=always`. The database is the
`Postgrees-custat` Docker container (postgres:17) on `127.0.0.1:5433`, **not**
the system postgresql cluster on 5432.

Routine deploy:

```bash
ssh lvivhp && cd ~/Projects/CU_statistics_bot
git pull
venv/bin/pip install -r requirements.txt   # when the pins change
sudo systemctl restart CU_statistics_bot
journalctl -u CU_statistics_bot -f
```

`init_db()` runs `alembic upgrade head` at startup, so schema changes need no
separate step.

### One-off: the volunteer_id migration and the unprivileged unit

Both want a restart, so do them in one window. Rehearsed end to end against a
`pg_dump` of production restored into a throwaway postgres:17 container.

**Run this in an interactive ssh session**, from a real terminal:

```bash
ssh lvivhp
cd ~/Projects/CU_statistics_bot
```

Not as a series of non-interactive one-liners. Several steps need a `sudo`
password, which requires a TTY (`ssh host 'sudo ...'` just fails with "sudo: a
password is required"), and `~/Projects/CU_statistics_bot` is a path **on the
server** -- running it locally lands in the wrong place or fails outright,
which is easy to miss when `&&` then short-circuits the rest of the line.

```bash
# Each command is self-contained, so it is safe to run them one at a time.
# Do not split a DSN into a `DB=...` variable for use in a later command: in
# any setup where each command gets a fresh shell the variable is gone by the
# next one, psql falls back to a local socket as the invoking user, and you get
# 'role "yaros" does not exist' -- which reads like a database fault rather
# than an empty variable.
#
# After step 3, deploy/dsn.sh does the extraction: psql "$(deploy/dsn.sh)" ...

# 0. Pre-flight. Note the row count: the checks in step 5 are relative to it,
#    because volunteers keep submitting until the bot stops.
psql "$(grep -E '^DATABASE_URL=' .env | sed -E 's/^DATABASE_URL="?//; s/"?$//; s#^postgresql\+asyncpg#postgresql#')" -c "SELECT count(*) AS before FROM statistics;"
git status --porcelain | grep -v '\.pyc'      # expect only mode changes
git diff --numstat -- . ':(exclude)*.pyc'      # expect 0 insertions, 0 deletions

# 1. Back up, and keep the current unit file for rollback.
#    Note the path: /home/yaros is drwxr-sr-x root:users, so your own home is
#    NOT writable -- only ~/Projects below it is. Avoid /tmp, which is a tmpfs
#    and would lose the backup on a reboot.
pg_dump --no-owner --no-acl "$(grep -E '^DATABASE_URL=' .env | sed -E 's/^DATABASE_URL="?//; s/"?$//; s#^postgresql\+asyncpg#postgresql#')" > ~/Projects/custat_pre_migration_$(date +%F).sql
ls -l ~/Projects/custat_pre_migration_*.sql    # sanity: roughly 80 KB, not empty
cp /etc/systemd/system/CU_statistics_bot.service ~/Projects/CU_statistics_bot.service.bak

# 2. Stop the bot. The new code and the old schema are not compatible.
sudo systemctl stop CU_statistics_bot

# 3. Deploy. core.fileMode=false clears the 644->755 drift that otherwise
#    blocks the pull; the tracked .pyc files are removed by this commit.
git config core.fileMode false
git checkout -- .
git pull origin main
venv/bin/pip install -r requirements.txt       # alembic is new

# 4. Migrate. The tables already exist, so stamp the baseline first.
venv/bin/python -m alembic stamp 0001
venv/bin/python -m alembic upgrade head
#    Expect: "0002: archived 104 orphaned row(s), deleted 1 command-answer
#    row(s), removed 104 orphan(s) from statistics"

# 5. Verify before starting anything. From 701 rows, expect 596 live.
psql "$(deploy/dsn.sh)" -c "SELECT count(*) AS live FROM statistics;"
psql "$(deploy/dsn.sh)" -c "SELECT count(*) AS archived FROM statistics_archive;"
psql "$(deploy/dsn.sh)" -c "SELECT count(*) AS unowned FROM statistics WHERE volunteer_id IS NULL;"
venv/bin/python -m alembic check               # "No new upgrade operations detected."

# 6. Drop root. The service user only needs group `users`, which already has
#    rwx on the project directory, so nothing is chowned.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin -G users cubot
sudo chgrp users .env && sudo chmod 640 .env   # currently 2775: world-readable
sudo cp deploy/CU_statistics_bot.service /etc/systemd/system/
sudo systemctl daemon-reload

# 7. Start. (On a first install use `start`; step 8 below uses `restart`,
#    which is the right verb for every later deploy.)
sudo systemctl start CU_statistics_bot

# 8. VERIFY IT IS ACTUALLY RUNNING. Do not skip this: `systemctl is-active`
#    reports "active" during an auto-restart cycle, so a service crash-looping
#    every RestartSec=5 looks healthy. It reported Result=success and
#    ExecMainStatus=0 through a twelve-hour outage.
#
#    Note the PID *before* restarting, so you can tell "restarted cleanly"
#    from "never restarted" -- both show a frozen counter and a live PID.
BEFORE=$(systemctl show CU_statistics_bot -p MainPID --value); echo "pid before: $BEFORE"
sudo systemctl restart CU_statistics_bot
sleep 25
systemctl show CU_statistics_bot -p NRestarts -p MainPID -p ActiveEnterTimestamp --no-pager
#    Healthy = MainPID is non-zero AND different from $BEFORE, and NRestarts
#    is unchanged over a second sample 25s later.
sleep 25; systemctl show CU_statistics_bot -p NRestarts -p MainPID --no-pager
#    Climbing NRestarts with MainPID=0 = crash loop; see below.

#    Startup completed only if sync_volunteers ran, which is after
#    validate_config and the schema upgrade. 29 volunteers (not 30) confirms it.
psql "$(deploy/dsn.sh)" -c "SELECT count(*) AS volunteers, count(tg_id) AS registered FROM volunteers;"

#    journalctl needs group adm or systemd-journal; `sudo journalctl -u
#    CU_statistics_bot -n 50` works regardless. Without it, reproduce the
#    startup in the foreground, which prints the real traceback:
#      timeout 20 venv/bin/python -m bot.main
```

If it crash-loops, run the config check first -- a missing config-sheet key
makes the bot exit during startup, and `Restart=always` turns that into a
silent five-second loop:

```bash
venv/bin/python deploy/check_config.py
```

Expect in the log: lexicon loaded, schema upgraded, then
`sync_volunteers: removed 1 volunteer(s) absent from the sheet: ['']` — that is
the blank-named row the old roster parser created, cleaning itself up.

Rollback:

```bash
sudo systemctl stop CU_statistics_bot
venv/bin/python -m alembic downgrade 0001      # restores tg_id and the archived rows
# or, if that misbehaves:  psql "$(deploy/dsn.sh)" < ~/Projects/custat_pre_migration_<date>.sql
git checkout 2e884be
sudo cp ~/Projects/CU_statistics_bot.service.bak /etc/systemd/system/CU_statistics_bot.service
sudo systemctl daemon-reload && sudo systemctl start CU_statistics_bot
```

If the hardened unit fails to start, drop `MemoryDenyWriteExecute` first — it
is the setting most likely to upset a compiled extension — then
`ProtectSystem`. The `.bak` copy is the fast way back.

## Security notes

Two things found on the host that are worth fixing but are not code changes:

- **`.env` is mode 2775**, so it is world-readable. It holds `BOT_TOKEN` and
  the Google service-account private key, which means any user on the box can
  read both. Step 6 above sets it to 640.
- **The Postgres containers publish on `0.0.0.0`** (`5433` and `5434`), so the
  databases are reachable from the network rather than only from localhost.
  Binding them to `127.0.0.1:5433:5432` would close that off; it needs a
  container restart, so it is left out of this window.

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
