from datetime import datetime
from bot.lexicon import Lexicon

class WeekOutOfRange(ValueError):
    """Raised for a week that falls outside the configured semester."""


def questions_in_week(current_week: int | None = None) -> int:
    if current_week is None:
        now = datetime.now()
        current_week= now.isocalendar()[1]
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    if check_week_in_range(current_week) is False:
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

def week_to_column_coords(week: int) -> str:
    """A1 column letters for a week's answers, e.g. "C F"."""
    first_index, last_index = week_to_indices(week)
    return f"{number_to_excel_column(first_index + 1)} {number_to_excel_column(last_index)}"

def week_to_indices(week: int) -> list[int]:
    """0-based, end-exclusive column span for a week's answers.

    Raises for a week outside the semester. It used to return None, and every
    caller subscripted the result immediately, so the real error surfaced as
    `TypeError: 'NoneType' object is not subscriptable`.
    """
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    if check_week_in_range(week) is False:
        raise WeekOutOfRange(
            f"week {week} is outside the semester "
            f"({Lexicon.START_DATE} to {Lexicon.END_DATE})"
        )
    pattern = Lexicon.QUESTION_PATTERN
    result = 3
    index = 0
    for i in range(week - start_week):
        result += int(pattern[i% len(pattern)])
        index = i+1
    return [result-1, result+int(pattern[index % len(pattern)]) - 1]

def check_week_in_range(week: int | None = None) -> bool:
    if week is None:
        week = datetime.now().isocalendar()[1]
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    end_week = datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y").isocalendar()[1]
    return start_week <= week <= end_week
