from datetime import datetime
from bot.lexicon import Lexicon, LexiconError, select_random_line, text
from babel.dates import format_date
from bot.config import config
import html
import random
import xml.etree.ElementTree as ET
from pathlib import Path
from bot.res.Bible_books_dict import BIBLE_BOOKS_UA

def display_name(full_name: str) -> str:
    """How to address a volunteer.

    The sheet stores "Surname Given", so the given name is the second token.
    Callers used `full_name.split()[1]` directly, which raised IndexError on
    any single-word name.
    """
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else full_name


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

BIBLE_PATH = Path(__file__).resolve().parent.parent / "res" / "Bible.xml"

_verses: list[str] | None = None


def load_bible_verses(path: Path | None = None) -> list[str]:
    """Pre-render every verse as the string the bot sends.

    Called once and cached. This used to run on *every* text message: the
    fallback handler re-parsed a 6.9 MB XML file and rebuilt a 31k-entry list
    of dicts each time, synchronously, on the event loop. Storing the finished
    strings keeps the resident cost to a few MB instead of holding the parsed
    tree or a dict per verse.
    """
    tree = ET.parse(path or BIBLE_PATH)
    verses: list[str] = []

    for book in tree.getroot().iter("book"):
        book_name = BIBLE_BOOKS_UA.get(book.get("number"), f"Книга {book.get('number')}")
        for chapter in book.iter("chapter"):
            chapter_number = chapter.get("number")
            for verse in chapter.iter("verse"):
                text = (verse.text or "").strip()
                if not text:
                    # A handful of verses are empty; rendering them produced
                    # a message reading "None".
                    continue
                verses.append(f"📖 {text}\n📍 {book_name} {chapter_number}:{verse.get('number')}")

    return verses


def random_bible_verse() -> str:
    global _verses
    if _verses is None:
        _verses = load_bible_verses()
    return random.choice(_verses)


def answer_sort_key(key: str) -> tuple[int, int, str]:
    """Order 'answer_N' keys numerically; keep anything unrecognised last.

    The totals used to be iterated straight off a set, so a user's categories
    came back in a different order every time they asked for their stats.
    """
    parts = key.split("_")
    if len(parts) > 1 and parts[1].isdigit():
        return (0, int(parts[1]), "")
    return (1, 0, key)


LABEL_WIDTH = 25


def question_label(key: str) -> str:
    """Display name for an answer_N key, falling back to the key itself.

    `key.split('_')[1]` raised on any key that was not answer_N, and a missing
    QUESTION_N_NAME raised while rendering a volunteer's own statistics.
    """
    parts = key.split("_")
    if len(parts) > 1 and parts[1].isdigit():
        try:
            return select_random_line(f"QUESTION_{parts[1]}_NAME")
        except LexiconError:
            pass
    return key


def pad_label(name: str) -> str:
    """Fixed-width label so the numbers line up in a monospace block."""
    if len(name) > LABEL_WIDTH:
        # Truncation and the threshold used to disagree: it cut at 23 while
        # testing for more than 25.
        return name[: LABEL_WIDTH - 2] + "…:"
    return f"{name}: ".ljust(LABEL_WIDTH, ".")


def format_all_stats_for_user(stats_list) -> str:
    """Render a volunteer's semester totals as HTML.

    This used to be sent as MarkdownV2 without escaping anything, so a
    question name containing '.', '-', '(' or '!' -- ordinary in Ukrainian
    labels -- made Telegram reject the message with a 400.
    """
    if not stats_list:
        return text('NO_STATS_TEXT', "Статистика недоступна.")

    keys = set()
    for stat in stats_list:
        keys.update(stat.stats.keys())

    totals = {key: 0 for key in sorted(keys, key=answer_sort_key)}
    for key in totals:
        for stat in stats_list:
            try:
                value = int(stat.stats.get(key, 0))
            except (TypeError, ValueError):
                value = 0
            totals[key] += value

    lines = [text('STATS_HEADER', "📊 Твоя статистика за семестр:"), ""]
    for key, value in totals.items():
        lines.append(f"<code>{html.escape(pad_label(question_label(key)))} {value}</code>")
    return "\n".join(lines)
