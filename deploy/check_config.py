"""Report configuration gaps without starting the bot.

Run this before a restart that includes a change to REQUIRED_KEYS. validate_config
raises on the first problem it finds, which makes a multi-key gap look like a
single one; this reports everything at once and exits non-zero if anything is
wrong.

    cd ~/Projects/CU_statistics_bot
    PYTHONPATH=$PWD venv/bin/python deploy/check_config.py

It reads whichever source the bot would read -- the database if the
configuration has been imported into it, the spreadsheet otherwise -- so a
pass here means the same thing a startup would. Read-only either way.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Importable whether run as deploy/check_config.py from the project root or
# copied elsewhere, as long as the cwd is the project.
for candidate in (Path(__file__).resolve().parents[1], Path.cwd()):
    if (candidate / "bot" / "lexicon.py").is_file():
        sys.path.insert(0, str(candidate))
        break
else:
    sys.exit("check_config.py: run this from the project root")

from bot import lexicon  # noqa: E402
from bot.lexicon import REQUIRED_KEYS, Lexicon, LexiconError  # noqa: E402

OPTIONAL_KEYS = (
    "NAME_NOT_RECOGNISED", "GREETING", "NOTHING_TO_CANCEL", "CANCELLED",
    "UNREGISTERED", "ANSWER_MUST_BE_A_NUMBER", "UNKNOWN_COMMAND", "SESSION_LOST",
    "NO_STATS_AVAILABLE", "NO_STATS_TEXT", "STATS_HEADER", "WEEK_NOT_FILLED",
    "WEEK_LABEL",
)


async def load() -> str:
    """Mirror bot/main.py exactly. A check that reads a different source from
    the bot is worse than no check: it reports on a configuration nobody runs.

    A database that cannot be reached is reported, not fatal: the bot would
    fall through to the spreadsheet too, and this script's job is to say what
    would happen, not to decide it.
    """
    from bot.db import database

    note = ""
    try:
        try:
            if not await lexicon.load_from_store():
                Lexicon.load_from_sheet()
        except LexiconError:
            # The database holds a configuration the bot would refuse. Say so;
            # falling back to the sheet here would report a passing check for a
            # configuration nobody is going to run.
            raise
        except Exception as e:
            note = f"could not read the configuration database ({e}); read the spreadsheet instead"
            Lexicon.load_from_sheet()
    finally:
        await database.dispose_db()
    return note


def main() -> int:
    try:
        note = asyncio.run(load())
    except Exception as e:
        # A configuration the bot would refuse to start on. Report it as a
        # problem rather than a traceback -- that is what this script is for.
        print(f"FAIL: loading the configuration failed: {e}")
        return 1

    if note:
        print(f"warning: {note}")
    print(f"source: the {lexicon.source()}")
    problems: list[str] = []

    missing = [k for k in REQUIRED_KEYS if getattr(Lexicon, k, None) in (None, "")]
    if missing:
        problems.append(f"missing required key(s): {', '.join(missing)}")
    print(f"required keys: {len(REQUIRED_KEYS) - len(missing)}/{len(REQUIRED_KEYS)} present")

    pattern = getattr(Lexicon, "QUESTION_PATTERN", "") or ""
    print(f"QUESTION_PATTERN: {pattern!r}")
    if not pattern.isdigit():
        problems.append(f"QUESTION_PATTERN must be digits, got {pattern!r}")
    else:
        most = max(int(d) for d in pattern)
        gaps = [
            key
            for n in range(1, most + 1)
            for key in (f"QUESTION_{n}", f"QUESTION_{n}_NAME")
            if getattr(Lexicon, key, None) in (None, "")
        ]
        print(f"  needs QUESTION_1..{most}: {'all present' if not gaps else 'MISSING ' + ', '.join(gaps)}")
        if gaps:
            problems.append(f"missing {', '.join(gaps)}")

    start_raw = getattr(Lexicon, "START_DATE", None)
    end_raw = getattr(Lexicon, "END_DATE", None)
    print(f"semester: {start_raw} .. {end_raw}")
    try:
        start = datetime.strptime(start_raw, "%d.%m.%Y")
        end = datetime.strptime(end_raw, "%d.%m.%Y")
        print(f"  ISO weeks {start.isocalendar()[1]}..{end.isocalendar()[1]}, same year: {start.year == end.year}")
        if start.year != end.year:
            problems.append("START_DATE and END_DATE fall in different calendar years")
        if end < start:
            problems.append("END_DATE is before START_DATE")
    except (TypeError, ValueError) as e:
        problems.append(f"START_DATE/END_DATE must be dd.mm.yyyy: {e}")

    print("\noptional keys (absent is fine, the code falls back):")
    for key in OPTIONAL_KEYS:
        state = "configured" if lexicon.variants(key) else "fallback"
        print(f"  {key:<26} {state}")

    if problems:
        print("\nFAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nOK: the bot will start with this config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
