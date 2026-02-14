from datetime import datetime
from bot.lexicon import Lexicon

def questions_in_week(current_week=None):
    if current_week is None:
        now = datetime.now()
        current_week= now.isocalendar()[1]
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    end_week = datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y").isocalendar()[1]
    if start_week > current_week or end_week < current_week:
        return 0
    pattern = Lexicon.QUESTION_PATTERN
    index = (current_week - start_week) % len(pattern)
    return int(pattern[index])

def number_to_excel_column(num: int):
    result = ""
    while num > 0:
        modulo = (num - 1) % 26
        result = chr(ord("A") + modulo) + result
        num = (num - modulo) // 26
    return result

def week_to_column_coords(week:int):
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    end_week = datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y").isocalendar()[1]
    if start_week > week or end_week < week:
        return
    pattern = Lexicon.QUESTION_PATTERN
    result = 3
    index = 0
    for i in range(week - start_week):
        result += int(pattern[i% len(pattern)]) 
        index = i+1
    return f"{number_to_excel_column(result)} {number_to_excel_column(result+int(pattern[index % len(pattern)]) - 1)}"


