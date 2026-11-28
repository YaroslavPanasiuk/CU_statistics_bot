"""Copy the config spreadsheet into Postgres, once.

The bot reads the database if it holds anything and the spreadsheet otherwise,
so this script is the cutover: before it runs, nothing changes; after it runs,
the sheet is the rollback rather than the source.

    cd ~/Projects/CU_statistics_bot
    PYTHONPATH=$PWD venv/bin/python deploy/migrate_config.py          # dry run
    PYTHONPATH=$PWD venv/bin/python deploy/migrate_config.py --write

A dry run is the default and prints exactly what would change. Writing needs
--write, and overwriting a configuration someone has already edited in the web
UI needs --force on top: re-running this after the cutover would otherwise
throw away every edit made since, silently, because the sheet is frozen in time
and the database is not.

Validation runs against the candidate before anything is written, so a
spreadsheet the bot could not start on is refused here rather than at 3am on
the next restart.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Importable whether run as deploy/migrate_config.py from the project root or
# copied elsewhere, as long as the cwd is the project.
for candidate in (Path(__file__).resolve().parents[1], Path.cwd()):
    if (candidate / "bot" / "lexicon.py").is_file():
        sys.path.insert(0, str(candidate))
        break
else:
    sys.exit("migrate_config.py: run this from the project root")

from bot import lexicon, registry  # noqa: E402
from bot.lexicon import LexiconError  # noqa: E402

#: How much of a value to show in the report. Long enough to recognise a line,
#: short enough that 44 keys fit on a screen.
EXCERPT = 72


def excerpt(value: str) -> str:
    one_line = value.replace("\n", "⏎")
    if len(one_line) <= EXCERPT:
        return one_line
    return one_line[: EXCERPT - 1] + "…"


def plan(sheet: dict[str, list[str]], stored: dict[str, list[str]]) -> dict:
    """What importing the sheet would do to the store."""
    described = set(registry.BY_KEY)
    return {
        "new": {k: v for k, v in sheet.items() if k in described and k not in stored},
        "changed": {
            k: (stored[k], v)
            for k, v in sheet.items()
            if k in described and k in stored and stored[k] != v
        },
        "unchanged": sorted(
            k for k, v in sheet.items()
            if k in described and stored.get(k) == v
        ),
        # Keys the registry does not describe are skipped, not stored: the web
        # UI only shows described keys, so an undescribed one would sit in the
        # database invisible and undeletable. Adding it to bot/registry.py is
        # the fix, and that is also where someone can read what it means.
        "undescribed": sorted(k for k in sheet if k not in described),
        # Keys only the database has. Left alone -- this script adds the
        # sheet's contents, it does not make the database a mirror.
        "only_stored": sorted(k for k in stored if k not in sheet),
    }


def render(result: dict, mapping: dict[str, list[str]]) -> str:
    lines: list[str] = []

    if result["new"]:
        lines.append(f"\n{len(result['new'])} key(s) would be added:")
        for key, values in sorted(result["new"].items()):
            lines.append(f"  + {key}")
            for value in values:
                lines.append(f"      {excerpt(value)}")

    if result["changed"]:
        lines.append(f"\n{len(result['changed'])} key(s) would be CHANGED:")
        for key, (was, now) in sorted(result["changed"].items()):
            lines.append(f"  ~ {key}")
            for value in was:
                lines.append(f"    - {excerpt(value)}")
            for value in now:
                lines.append(f"    + {excerpt(value)}")

    if result["unchanged"]:
        lines.append(f"\n{len(result['unchanged'])} key(s) already match the sheet.")

    if result["undescribed"]:
        lines.append(
            f"\n{len(result['undescribed'])} key(s) in the sheet are not described in "
            "bot/registry.py and will be SKIPPED:"
        )
        for key in result["undescribed"]:
            lines.append(f"  ? {key}  —  {excerpt(mapping[key][0])}")
        lines.append(
            "    Add a Field for each in bot/registry.py to import it. A key the "
            "registry does not describe cannot be shown or edited in the web UI."
        )

    if result["only_stored"]:
        lines.append(
            f"\n{len(result['only_stored'])} key(s) exist only in the database and are "
            f"left untouched: {', '.join(result['only_stored'])}"
        )

    return "\n".join(lines)


def to_import(result: dict, mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    keys = set(result["new"]) | set(result["changed"]) | set(result["unchanged"])
    return {k: mapping[k] for k in keys}


async def run(write: bool, force: bool) -> int:
    from bot.db import configstore, database

    print("reading the config spreadsheet...")
    mapping = lexicon.sheet_mapping()
    if not mapping:
        print("FAIL: the config spreadsheet produced no keys at all.")
        return 1
    print(f"  {len(mapping)} key(s), {sum(len(v) for v in mapping.values())} variant(s)")

    stored = await configstore.load()
    result = plan(mapping, stored)
    candidate = to_import(result, mapping)

    print("\nchecking the candidate configuration...")
    try:
        lexicon.validate_mapping(candidate)
    except LexiconError as exc:
        print(f"FAIL: the bot would refuse this configuration: {exc}")
        print("\nNothing was written. Fix the spreadsheet and run this again.")
        return 1
    print("  the bot would start on this configuration")

    print(render(result, mapping))

    if not result["new"] and not result["changed"]:
        print("\nNothing to do: the database already matches the sheet.")
        return 0

    if not write:
        print("\nDry run — nothing was written. Re-run with --write to apply.")
        return 0

    if stored and result["changed"] and not force:
        print(
            "\nREFUSED: the database already holds a different value for "
            f"{len(result['changed'])} key(s), which means someone has edited them "
            "in the web UI since the last import. The spreadsheet is frozen in "
            "time and the database is not, so importing would throw those edits "
            "away.\n\nRe-run with --force if that is really what you want."
        )
        return 1

    written = await configstore.import_mapping(candidate, "migrate_config.py")
    version = await configstore.version()
    print(f"\nWrote {written} key(s). Configuration version is now {version}.")
    print(
        "The bot picks this up within 30 seconds, or immediately on "
        "/reload_config. Until then it is still serving the previous "
        "configuration."
    )
    await database.dispose_db()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true",
        help="actually write to the database (default is a dry run)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite values that differ from the sheet, discarding web UI edits",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.write, args.force))
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
