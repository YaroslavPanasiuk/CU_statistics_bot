import json
import os

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised for a .env the bot cannot sensibly run with."""


def parse_admin_ids(raw: str | None) -> tuple[int, ...]:
    """Parse ADMIN_IDS once, here.

    IsAdmin used to re-split the raw string and call int() on every incoming
    message, so a trailing comma, a space after one, or an empty value raised
    ValueError for *every* update rather than at startup.
    """
    if not raw:
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            raise ConfigError(f"ADMIN_IDS contains a non-numeric entry: {part!r}") from None
        if value not in ids:
            ids.append(value)
    # A tuple, not a set: the first entry is the admin who receives scheduler
    # reports, so order has to survive parsing.
    return tuple(ids)


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_IDS = parse_admin_ids(os.getenv("ADMIN_IDS"))
    DATABASE_URL = os.getenv("DATABASE_URL")
    GOOGLE_SHEET_CONFIG_URL = os.getenv("GOOGLE_SHEET_CONFIG_URL")
    GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
    LOCALE = "uk"
    TIMEZONE = os.getenv("TZ")

    # How far back /fill_stats reaches, so a Sunday or Monday submission lands
    # on the week that just ended.
    LAG_THRESHOLD_DAYS = 3

    # Longest answer accepted for a single question.
    MAX_ANSWER_LENGTH = 200

    try:
        GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDS_JSON", "{}"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"GOOGLE_CREDS_JSON is not valid JSON: {e}") from None

    if not BOT_TOKEN:
        raise ConfigError("BOT_TOKEN is not set")
    if not DATABASE_URL:
        raise ConfigError("DATABASE_URL is not set")
    if not ADMIN_IDS:
        raise ConfigError("ADMIN_IDS is empty: no one would be able to administer the bot")


config = Config()
