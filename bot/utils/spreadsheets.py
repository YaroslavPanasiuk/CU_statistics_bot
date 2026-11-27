import asyncio
import time
from datetime import datetime

import gspread
from gspread.exceptions import APIError

from bot.config import config
from bot.db import database
from bot.lexicon import Lexicon
from bot.utils.maths import questions_in_week, week_to_column_coords, week_to_indices

# Every operation used to build a fresh service-account client and re-open the
# spreadsheet by URL -- three extra round trips per call, against a quota of
# 60 requests per minute per user. These handles are cached instead.
_client: gspread.Client | None = None
_worksheet_cache: dict[tuple[str, str], gspread.Worksheet] = {}

# Roster from the last load_volunteer_list(), so looking up one volunteer's row
# does not re-download the whole sheet.
_roster: list[str] | None = None

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def reset_sheet_cache() -> None:
    """Drop cached handles. Call after the config sheet is reloaded, since
    WORKSHEET_NAME may now point somewhere else."""
    global _client, _roster
    _client = None
    _roster = None
    _worksheet_cache.clear()


def data_worksheet() -> gspread.Worksheet:
    global _client
    key = (config.GOOGLE_SHEET_URL, Lexicon.WORKSHEET_NAME)
    if key not in _worksheet_cache:
        if _client is None:
            _client = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        spreadsheet = _client.open_by_url(config.GOOGLE_SHEET_URL)
        _worksheet_cache[key] = spreadsheet.worksheet(Lexicon.WORKSHEET_NAME)
    return _worksheet_cache[key]


def with_retry(fn, *args, attempts: int = 4, **kwargs):
    """Run a Sheets call, backing off through rate limits and 5xx.

    Synchronous on purpose: callers run it inside asyncio.to_thread, so the
    sleep never blocks the event loop.
    """
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except APIError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status not in RETRYABLE_STATUSES or attempt == attempts:
                raise
            print(f"Sheets API {status}; retrying in {delay:.0f}s (attempt {attempt}/{attempts})")
            time.sleep(delay)
            delay *= 2


# These resolve the worksheet *inside* the function body, so a cache miss
# (which opens the spreadsheet by URL) happens on the worker thread rather
# than on the event loop.
def read_all_values() -> list[list[str]]:
    return with_retry(data_worksheet().get_all_values)


def read_range(a1_range: str) -> list[list[str]]:
    return with_retry(data_worksheet().get_values, range_name=a1_range)


def write_values(a1_range: str, values: list[list[str]]) -> None:
    with_retry(
        data_worksheet().batch_update,
        [{"range": a1_range, "values": values}],
        value_input_option="USER_ENTERED",
    )

async def load_volunteer_list() -> list[str]:
    """Return the volunteer roster from column B of the data sheet.

    Raises on failure. `sync_volunteers` deletes volunteers that are absent
    from this list, so a Sheets outage must never be mistaken for an empty
    roster.
    """
    global _roster
    values = await asyncio.to_thread(read_all_values)
    _roster = volunteer_names(values)
    return _roster


def volunteer_names(values: list[list[str]]) -> list[str]:
    """Names from column B, skipping the two header rows.

    This used to slice `[2:-1]`, assuming the sheet ended in a totals row. It
    actually ends in blank padding rows, so every blank cell came back as a
    volunteer named "" -- 42 entries for 29 volunteers, which created an
    empty-named row in the database that showed up as a blank registration
    button. Select on a non-empty name instead.
    """
    names: list[str] = []
    for row in values[2:]:
        if len(row) < 2:
            continue
        name = row[1].strip()
        if name:
            names.append(name)
    return names

async def volunteer_to_row_coord(volunteer) -> int:
    """1-based sheet row for a volunteer, or -1 if absent.

    Reads the roster cached by the last load_volunteer_list() instead of
    re-downloading the entire sheet on every export.
    """
    roster = _roster if _roster is not None else await load_volunteer_list()
    for i, name in enumerate(roster):
        if volunteer.full_name == name:
            return i + 3
    return -1


async def export_stats_to_sheet(volunteer, week):
    try:
        stats = await database.get_user_statistics(volunteer_id=volunteer.id, week=week)
        if not stats:
            print(f"Export skipped: no stored statistics for {volunteer.full_name} week {week}")
            return

        row = await volunteer_to_row_coord(volunteer)
        if row < 0:
            # Previously this formatted an invalid A1 range like "C-1:F-1" and
            # relied on the except below to swallow the resulting error.
            print(f"Export skipped: {volunteer.full_name} is not in the sheet roster")
            return

        first_column, last_column = week_to_column_coords(week).split()
        a1_range = f"{first_column}{row}:{last_column}{row}"
        values = [list(stats[0].stats.values())]

        await asyncio.to_thread(write_values, a1_range, values)
    except Exception as e:
        print(f"Export failed: {e}")


async def import_stats_from_sheet():
    try:
        values = await asyncio.to_thread(read_all_values)
    except Exception as e:
        # The import loop below used to sit outside this try, so a Sheets
        # failure printed here and then raised NameError on `values`.
        print(f"Import failed: {e}")
        return

    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    end_week = datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y").isocalendar()[1]
    current_week = datetime.now().isocalendar()[1]
    if current_week > end_week:
        print(f"Current week {current_week} is greater than end week {end_week}. No stats to import.")
        return
    if current_week < start_week:
        print(f"Current week {current_week} is less than start week {start_week}. No stats to import.")
        return

    for row in values[2:]:
        volunteer_name = row[1].strip() if len(row) > 1 else ""
        if not volunteer_name:
            continue
        volunteer = await database.get_user_by_full_name(volunteer_name)
        if volunteer is None:
            print(f"Skipping {volunteer_name} - not in the database")
            continue
        if volunteer.tg_id is None:
            print(f"Skipping {volunteer_name} - not registered")
            continue
        for offset in range(current_week - start_week + 1):
            week = offset + start_week
            first_index, last_index = week_to_indices(week)
            answers = row[first_index:last_index]
            stats_dict = {f"answer_{n + 1}": value for n, value in enumerate(answers)}
            try:
                await database.save_user_stats(
                    volunteer_id=volunteer.id,
                    week=week,
                    data_dict=stats_dict
                )
            except Exception as e:
                print(f"Failed to save stats for {volunteer_name} in week {week}: {e}")





async def fetch_users_with_no_stats(week):
    try:
        end_column = week_to_column_coords(week).split()[1]
        a1_range = f"B1:{end_column}1000"
        question_count = questions_in_week(week)

        records = await asyncio.to_thread(read_range, a1_range)
        records = records[2:]
        volunteers = [record[0] for record in records]
        stats = [record[-question_count:] for record in records]
        result = []
        for i, volunteer in enumerate(volunteers):
            if "".join(stats[i]) == "":
                result.append(volunteer)
        return result



    except Exception as e:
        print(f"Import failed: {e}")

