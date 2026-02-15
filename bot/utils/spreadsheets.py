import gspread
from bot.config import config
from bot.db import database
from bot.utils.maths import week_to_column_coords, questions_in_week

def load_volunteer_list():
        try:
            gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
            sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
            worksheet = sh.get_worksheet(0)
            
            records = worksheet.get_all_values()

            return [item[1] for item in records[2:-1]]
        
        except Exception as e:
            print(f"Failed to load strings from Google Sheets: {e}")


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
        worksheet = sh.get_worksheet(0)

        row = await volunteer_to_row_coord(tg_id)
        columns = week_to_column_coords(week)
        range = f"{columns.split()[0]}{row}:{columns.split()[1]}{row}"

        worksheet.batch_update([{
            'range': range,
            'values': [list(stats[0][1].values())]
        }])
        
        
    except Exception as e:
        print(f"Export failed: {e}")


def fetch_users_with_no_stats(week):
    try:
        gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        sh = gc.open_by_url(config.GOOGLE_SHEET_URL)
        worksheet = sh.get_worksheet(0)

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
        print(f"Export failed: {e}")