from datetime import datetime
from bot.lexicon import Lexicon
def week_num_to_dates(week:int):
    year = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").year
    monday = datetime.fromisocalendar(year, week, 1).date()
    sunday = datetime.fromisocalendar(year, week, 7).date()
    return f'{monday.strftime('%d %b')} - {sunday.strftime('%d %b')}'