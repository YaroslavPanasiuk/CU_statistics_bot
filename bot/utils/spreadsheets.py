from bot.lexicon import Lexicon
import gspread
from bot.config import config
from datetime import datetime
from bot.db import database
from bot.utils.maths import week_to_column_coords, week_to_indices, questions_in_week

def load_volunteer_list() -> list[str]:
    """Return the volunteer roster from column B of the data sheet.

    Raises on failure. `sync_volunteers` deletes volunteers that are absent
    from this list, so a Sheets outage must never be mistaken for an empty
    roster.
    """
    gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
    sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
    worksheet = sh.worksheet(Lexicon.WORKSHEET_NAME)
    return volunteer_names(worksheet.get_all_values())


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

async def volunteer_to_row_coord(tg_id):
    volunteer = await database.get_user_by_tg_id(tg_id)
    volunteers = load_volunteer_list()
    for i, v in enumerate(volunteers):
         if volunteer.full_name == v:
              return i + 3
    return -1


async def export_stats_to_sheet(tg_id, week):
    try:
        stats = await database.get_user_statistics(week=week, tg_id=tg_id)
        gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
        worksheet = sh.worksheet(Lexicon.WORKSHEET_NAME)

        row = await volunteer_to_row_coord(tg_id)
        columns = week_to_column_coords(week)
        range = f"{columns.split()[0]}{row}:{columns.split()[1]}{row}"

        worksheet.batch_update([{
            'range': range,
            'values': [list(stats[0][1].values())]
        }]
        , value_input_option='USER_ENTERED')


    except Exception as e:
        print(f"Export failed: {e}")


async def import_stats_from_sheet():
    try:
        gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
        worksheet = sh.worksheet(Lexicon.WORKSHEET_NAME)
        values = worksheet.get_all_values()
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
                    tg_id=volunteer.tg_id,
                    week=week,
                    data_dict=stats_dict
                )
            except Exception as e:
                print(f"Failed to save stats for {volunteer_name} in week {week}: {e}")





def fetch_users_with_no_stats(week):
    try:
        gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
        worksheet = sh.worksheet(Lexicon.WORKSHEET_NAME)

        end_column = week_to_column_coords(week).split()[1]
        start_column = "B"
        range = f"{start_column}1:{end_column}1000"
        question_count = questions_in_week(week)

        records = worksheet.get_values(range_name=range)
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

