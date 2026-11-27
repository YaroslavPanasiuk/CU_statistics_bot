import asyncio
import logging
import random
import re
from collections.abc import Callable, Mapping
from datetime import datetime

import gspread

from bot import registry
from bot.config import config
from aiogram.filters import Filter
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Keys the bot cannot operate without, and the question family that is only
# required when the schedule asks for it, both come from the registry -- see
# bot/registry.py for what each key means and why.
REQUIRED_KEYS = registry.REQUIRED_KEYS

# How often a running bot asks the database whether the configuration changed.
# One trivial SELECT; the delay between saving in the web UI and seeing the
# effect in Telegram is what this number buys.
POLL_SECONDS = 30

# Keys whose value is parsed rather than shown. Several variants of a message
# are a feature; several variants of a date are a coin toss over when the
# semester starts, so the web UI must not offer them and validation rejects
# them if they arrive some other way.
SINGLE_VALUE_KINDS = (registry.Kind.DATE, registry.Kind.PATTERN, registry.Kind.WORKSHEET)

# The live configuration: key -> its interchangeable phrasings, in order.
# Replaced wholesale, never edited in place, so a reader can never observe
# half of one configuration and half of another.
_variants: dict[str, list[str]] = {}

# The config_version this cache was built from, so polling can tell whether
# anything changed. None means the cache did not come from the database.
_version: int | None = None

# A version that failed validation. Retrying it every 30 seconds would fill
# the log with the same complaint; the next edit changes the version and the
# bot tries again on its own.
_rejected_version: int | None = None

# Keys load_from_sheet() set directly on the class, remembered so that a later
# load from the database can clear them. Otherwise a value deleted in the web
# UI would go on being served from a stale class attribute.
_sheet_keys: set[str] = set()


class LexiconError(Exception):
    """Raised for a configuration the bot cannot sensibly run with."""


class _LexiconMeta(type):
    """Serves an attribute read of any configured key out of the cache.

    Real class attributes still win -- that is what keeps load_from_sheet()'s
    setattr and the tests' monkeypatch.setattr working unchanged.
    """

    def __getattr__(cls, name: str) -> str:
        values = _variants.get(name)
        if not values:
            raise AttributeError(name)
        # Every direct attribute read in the bot is of a parsed key (the dates,
        # QUESTION_PATTERN, WORKSHEET_NAME), so the first variant is the
        # answer. Message keys go through select_random_line()/text(), which
        # see the whole list.
        return values[0]


class Lexicon(metaclass=_LexiconMeta):
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
                    _sheet_keys.add(clean_key)


def variants(key: str) -> list[str]:
    """Every phrasing configured for a key, newest source first.

    The one place that knows a sheet value is ';;'-separated while a database
    value arrives as rows. Readers below just get a list.
    """
    raw = Lexicon.__dict__.get(key)
    if isinstance(raw, str) and raw != "":
        return raw.split(';;')
    return list(_variants.get(key, ()))


def select_random_line(key):
    values = variants(key)
    if not values:
        raise LexiconError(f"the configuration has no key {key!r}")
    return random.choice(values)


def text(key: str, default: str) -> str:
    """A configured string with a built-in fallback.

    Lets these strings be customised without obliging every deployment to add
    the key: validate_config only insists on the keys the bot genuinely cannot
    work without. Supports variants like select_random_line.
    """
    return random.choice(variants(key) or [default])


def fill(key: str, *args) -> str:
    """Substitute into a configured template, tolerating stray braces.

    `select_random_line(key).format(...)` raised on any literal '{' or '}' a
    person happened to type into the configuration.
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
        if message.text is None:
            return False
        # Keyboard labels are produced by select_random_line(), which picks one
        # of the variants. Comparing against the whole raw value meant that
        # adding a second variant to a key silently killed its button: the
        # label no longer equalled the stored string.
        return message.text in variants(self.key)


def fill_default(key: str, default: str, *args) -> str:
    """text() plus substitution, tolerating stray braces."""
    template = text(key, default)
    try:
        return template.format(*args)
    except (IndexError, KeyError, ValueError):
        logger.warning(f"Lexicon key {key!r} has a malformed placeholder; sending it unsubstituted")
        return template


def validate_config() -> None:
    """Check the live configuration, loudly."""
    _validate(variants)


def validate_mapping(mapping: Mapping[str, list[str]]) -> None:
    """Check a configuration that is not live yet.

    This is what makes a reload fail-safe: a candidate is validated while the
    bot is still serving the previous one, so a bad edit in the web UI is
    rejected instead of taking the bot down. The 12-hour outage this guards
    against came from the opposite order -- validate what is already loaded,
    and the only way to report a problem is to exit.
    """
    _validate(lambda key: list(mapping.get(key, ())))


def _validate(lookup: Callable[[str], list[str]]) -> None:
    """Every one of these produced a confusing runtime failure instead.

    A missing key raised AttributeError inside a handler, and a semester whose
    dates land in different calendar years made check_week_in_range
    unsatisfiable, so the bot silently declared itself permanently
    unavailable.
    """
    def one(key: str) -> str:
        values = lookup(key)
        return values[0] if values else ""

    missing = [key for key in REQUIRED_KEYS if not lookup(key)]
    if missing:
        raise LexiconError(f"the configuration is missing required key(s): {', '.join(missing)}")

    for field in registry.FIELDS:
        if field.kind in SINGLE_VALUE_KINDS and len(lookup(field.key)) > 1:
            raise LexiconError(
                f"{field.key} is read as a value, not shown as a message, so it "
                f"must have exactly one variant (it has {len(lookup(field.key))})"
            )

    start_date, end_date = one('START_DATE'), one('END_DATE')
    try:
        start = datetime.strptime(start_date, "%d.%m.%Y")
        end = datetime.strptime(end_date, "%d.%m.%Y")
    except ValueError as e:
        raise LexiconError(f"START_DATE/END_DATE must be dd.mm.yyyy: {e}") from None

    if end < start:
        raise LexiconError(f"END_DATE ({end_date}) is before START_DATE ({start_date})")

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

    pattern = one('QUESTION_PATTERN')
    if not pattern or not pattern.isdigit():
        raise LexiconError(f"QUESTION_PATTERN must be a non-empty string of digits, got {pattern!r}")
    if set(pattern) == {"0"}:
        raise LexiconError("QUESTION_PATTERN is all zeroes: no week would ask anything")

    # Enough QUESTION_N / QUESTION_N_NAME keys for the busiest week.
    most_questions = max(int(digit) for digit in pattern)
    for key in registry.question_keys(pattern):
        if not lookup(key):
            raise LexiconError(
                f"QUESTION_PATTERN asks up to {most_questions} questions, "
                f"but the configuration has no {key}"
            )


async def load_from_store() -> bool:
    """Replace the live configuration with what the database holds.

    Returns False when the database holds no configuration at all, which is
    how a deployment that has not run migrate_config.py yet -- or one rolled
    back by emptying the tables -- falls through to the spreadsheet.

    Raises LexiconError if what it holds would not run, leaving the previous
    configuration in place.
    """
    from bot.db import configstore

    global _version, _rejected_version
    mapping = await configstore.load()
    if not mapping:
        return False

    version = await configstore.version()
    validate_mapping(mapping)

    _variants.clear()
    _variants.update(mapping)
    # A key the sheet set is a key the database now owns. Left in place it
    # would shadow the database copy forever, since real class attributes win.
    for key in _sheet_keys:
        if hasattr(Lexicon, key) and key in Lexicon.__dict__:
            delattr(Lexicon, key)
    _sheet_keys.clear()
    _version = version
    _rejected_version = None
    logger.info(f"loaded {len(mapping)} configuration keys from the database (version {version})")
    return True


async def refresh_if_stale() -> bool:
    """Reload when the database reports a newer configuration.

    This is how an edit in the web UI reaches a running bot. Returns True only
    when an already-loaded configuration was replaced: a first load is not an
    edit, and reporting it as one sends whoever reads the startup log looking
    for a change nobody made.
    """
    from bot.db import configstore

    global _rejected_version
    current = await configstore.version()
    if current == _version or current == _rejected_version:
        return False

    was_loaded = _version is not None
    try:
        if not await load_from_store():
            return False
    except LexiconError as exc:
        # Keep serving the configuration that works and wait for the next edit.
        _rejected_version = current
        logger.error(f"configuration version {current} rejected, keeping the previous one: {exc}")
        return False

    # WORKSHEET_NAME may now name a different tab.
    from bot.utils.spreadsheets import reset_sheet_cache
    reset_sheet_cache()
    return was_loaded


async def poll_for_changes(interval: int = POLL_SECONDS) -> None:
    """Watch the database for configuration edits until cancelled.

    Every failure here is swallowed on purpose. This task exists to pick up
    edits; a database hiccup should delay that, not end the loop and leave the
    bot silently unable to ever see another edit.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await refresh_if_stale()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"configuration poll failed, retrying in {interval}s: {exc}")


def source() -> str:
    """Where the live configuration came from, for logs and /reload_config."""
    return "database" if _version is not None else "spreadsheet"
