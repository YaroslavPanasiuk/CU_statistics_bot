from datetime import datetime
from bot.lexicon import Lexicon, select_random_line
from babel.dates import format_date
from bot.config import config
import xml.etree.ElementTree as ET
import random
from bot.res.Bible_books_dict import BIBLE_BOOKS_UA

def week_num_to_dates(week:int):
    year = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").year
    monday = datetime.fromisocalendar(year, week, 1).date()
    sunday = datetime.fromisocalendar(year, week, 7).date()
    monday_format = "d MMMM"
    if monday.month == sunday.month:
        monday_format = "d"
    monday_str = format_date(monday, format=monday_format, locale=config.LOCALE)
    sunday_str = format_date(sunday, format="d MMMM", locale=config.LOCALE)
    return f'{monday_str} - {sunday_str}'

def random_bible_verse():
    tree = ET.parse("bot/res/Bible.xml")
    root = tree.getroot()

    all_data = []

    for testament in root.findall('testament'):
        t_name = testament.get('name')

        for book in testament.findall('book'):
            b_num = book.get('number')
            b_name = BIBLE_BOOKS_UA.get(b_num, f"Книга {b_num}")

            for chapter in book.findall('chapter'):
                c_num = chapter.get('number')

                for verse in chapter.findall('verse'):
                    v_num = verse.get('number')
                    v_text = verse.text

                    all_data.append({
                        "testament": t_name,
                        "book": b_name,
                        "chapter": c_num,
                        "verse": v_num,
                        "text": v_text
                    })

    # Pick a random entry
    selection = random.choice(all_data)

    return (
        f"📖 {selection['text']}\n"
        f"📍 {selection['book']} {selection['chapter']}:{selection['verse']}"
    )

def answer_sort_key(key: str) -> tuple[int, int, str]:
    """Order 'answer_N' keys numerically; keep anything unrecognised last.

    The totals used to be iterated straight off a set, so a user's categories
    came back in a different order every time they asked for their stats.
    """
    parts = key.split("_")
    if len(parts) > 1 and parts[1].isdigit():
        return (0, int(parts[1]), "")
    return (1, 0, key)


def format_all_stats_for_user(stats_list):
    if not stats_list:
        return "Статистика недоступна."
    keys = set()
    for stat in stats_list:
        keys.update(stat[1].keys())

    totals = {key: 0 for key in sorted(keys, key=answer_sort_key)}
    for key in totals:
        for stat in stats_list:
            try:
                value = int(stat[1].get(key, 0))
            except Exception:
                value = 0
            totals[key] += value

    text = "📊 Твоя статистика за семестр: \n\n"
    for key, value in totals.items():
        key_name = select_random_line(f"QUESTION_{key.split('_')[1]}_NAME")
        text += f"`{(key_name[:23] + '…:') if len(key_name) > 25 else f"{key_name}: ".ljust(25, ".")} {value}`\n"
    return text
