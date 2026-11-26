"""Test environment.

`bot.config` validates BOT_TOKEN at import time and `load_dotenv()` would
otherwise pull in the developer's real `.env`. Setting these first wins, because
python-dotenv does not override variables already present in the environment.
"""
import os

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")
# Integration tests run against TEST_DATABASE_URL when it is set; unit tests
# never open a connection, so the placeholder is enough for them.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL") or "postgresql+asyncpg://test@localhost/test",
)
os.environ.setdefault("GOOGLE_SHEET_CONFIG_URL", "https://example.invalid/config")
os.environ.setdefault("GOOGLE_SHEET_URL", "https://example.invalid/data")
os.environ.setdefault("GOOGLE_CREDS_JSON", "{}")
os.environ.setdefault("TZ", "Europe/Kyiv")

import pytest

from bot.lexicon import Lexicon


@pytest.fixture
def semester(monkeypatch):
    """Configure a semester the way the config sheet would.

    01.09.2025 is a Monday in ISO week 36; 21.12.2025 falls in week 51.
    The pattern "323" means week 36 asks 3 questions, week 37 asks 2,
    week 38 asks 3, then it repeats.
    """
    monkeypatch.setattr(Lexicon, "START_DATE", "01.09.2025", raising=False)
    monkeypatch.setattr(Lexicon, "END_DATE", "21.12.2025", raising=False)
    monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "323", raising=False)
    return {"start_week": 36, "end_week": 51, "pattern": "323"}
