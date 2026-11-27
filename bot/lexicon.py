import logging
import random
import re
from datetime import datetime

import gspread
from bot.config import config
from aiogram.filters import Filter
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Keys the bot cannot operate without. QUESTION_N / QUESTION_N_NAME are
# checked separately, against the question count the pattern implies.
#
# Only add a key here after confirming it exists in the live config sheet --
# `deploy/check_config.py` reports that. NO_STATS_AVAILABLE was added from
# reading the code alone, was absent from the sheet, and took the bot down in
# a five-second restart loop on first deploy. Anything that is merely a
# message belongs in text()/fill_default() with a fallback instead.
REQUIRED_KEYS = (
    "START_DATE",
    "END_DATE",
    "QUESTION_PATTERN",
    "WORKSHEET_NAME",
    "SELECT_YOUR_NAME",
    "REGISTRATION_COMPLETE",
    "NOT_REGISTERED",
    "SELECT_WEEK",
    "SELECTED_WEEK",
    "STATISTICS_GATHERED",
    "BOT_UNAVAILABLE",
    "SEMESTER_NOT_STARTED",
    "SEMESTER_OVER",
    "FILL_STATISTICS",
    "SELECT_PREVIOUS_WEEK",
    "GET_MY_STATS",
    "REMINDER1",
    "REMINDER2",
    "REMINDER3",
)


class LexiconError(Exception):
    """Raised for a config sheet the bot cannot sensibly run with."""


class Lexicon:
    START_WELCOME = "Welcome!"
    ASK_FULLNAME = "Enter your name:"
    SUCCESS_ACTION = "Done!"

    @classmethod
    def load_from_sheet(cls):
        """Load every key/value pair from the config spreadsheet.

        Raises. This used to swallow the exception, so a bad sheet left the
        bot running with only the three hardcoded defaults above and then
        raising AttributeError on Lexicon.START_DATE for every single
        interaction -- a failure that looked like a dozen unrelated bugs.
        """
        gc = gspread.service_account_from_dict(config.GOOGLE_CREDS)
        sh = gc.open_by_url(config.GOOGLE_SHEET_CONFIG_URL)
        worksheet = sh.get_worksheet(0)

        for row in worksheet.get_all_values():
            if len(row) >= 2:
                raw_key, value = row[0], row[1]
                clean_key = re.sub(r'\W+', '_', raw_key).strip('_').upper()
                if clean_key:
                    setattr(cls, clean_key, value)

def select_random_line(key):
    value = getattr(Lexicon, key, None)
    if value is None:
        raise LexiconError(f"the config sheet has no key {key!r}")
    return random.choice(value.split(';;'))


def text(key: str, default: str) -> str:
    """A sheet-authored string with a built-in fallback.

    Lets these strings be customised from the config sheet without obliging
    every deployment to add the key: validate_config only insists on the keys
    the bot genuinely cannot work without. Supports ';;' variants like
    select_random_line.
    """
    value = getattr(Lexicon, key, None)
    if not value:
        return default
    return random.choice(value.split(';;'))


def fill(key: str, *args) -> str:
    """Substitute into a sheet-authored template, tolerating stray braces.

    `select_random_line(key).format(...)` raised on any literal '{' or '}' a
    person happened to type into the config sheet.
    """
    template = select_random_line(key)
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        logger.warning(f"Lexicon key {key!r} has a malformed placeholder; sending it unsubstituted")
        return template

class LexiconFilter(Filter):
    def __init__(self, key: str):
        self.key = key

    async def __call__(self, message: Message) -> bool:
        raw = getattr(Lexicon, self.key, None)
        if raw is None or message.text is None:
            return False
        # Keyboard labels are produced by select_random_line(), which picks one
        # of the ';;'-separated variants. Comparing against the whole raw value
        # meant that adding a second variant to a key silently killed its
        # button: the label no longer equalled the stored string.
        return message.text in raw.split(';;')


def fill_default(key: str, default: str, *args) -> str:
    """text() plus substitution, tolerating stray braces in the sheet."""
    template = text(key, default)
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        logger.warning(f"Lexicon key {key!r} has a malformed placeholder; sending it unsubstituted")
        return template


def validate_config() -> None:
    """Check the config sheet at startup, loudly.

    Every one of these produced a confusing runtime failure instead:
    a missing key raised AttributeError inside a handler, and a semester whose
    dates land in different calendar years made check_week_in_range
    unsatisfiable, so the bot silently declared itself permanently
    unavailable.
    """
    missing = [key for key in REQUIRED_KEYS if getattr(Lexicon, key, None) in (None, "")]
    if missing:
        raise LexiconError(f"the config sheet is missing required key(s): {', '.join(missing)}")

    try:
        start = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y")
        end = datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y")
    except ValueError as e:
        raise LexiconError(f"START_DATE/END_DATE must be dd.mm.yyyy: {e}") from None

    if end < start:
        raise LexiconError(f"END_DATE ({Lexicon.END_DATE}) is before START_DATE ({Lexicon.START_DATE})")

    if start.year != end.year:
        raise LexiconError(
            f"the semester must fall within one calendar year, but START_DATE is "
            f"{start.year} and END_DATE is {end.year}. All week arithmetic uses bare "
            f"ISO week numbers, so a semester spanning New Year would make the bot "
            f"permanently unavailable."
        )

    start_week, end_week = start.isocalendar()[1], end.isocalendar()[1]
    if start_week > end_week:
        raise LexiconError(
            f"START_DATE is in ISO week {start_week} but END_DATE is in week {end_week}"
        )

    pattern = Lexicon.QUESTION_PATTERN
    if not pattern or not pattern.isdigit():
        raise LexiconError(f"QUESTION_PATTERN must be a non-empty string of digits, got {pattern!r}")
    if set(pattern) == {"0"}:
        raise LexiconError("QUESTION_PATTERN is all zeroes: no week would ask anything")

    # Enough QUESTION_N / QUESTION_N_NAME keys for the busiest week.
    most_questions = max(int(digit) for digit in pattern)
    for n in range(1, most_questions + 1):
        for key in (f"QUESTION_{n}", f"QUESTION_{n}_NAME"):
            if getattr(Lexicon, key, None) in (None, ""):
                raise LexiconError(
                    f"QUESTION_PATTERN asks up to {most_questions} questions, "
                    f"but the config sheet has no {key}"
                )
