"""Tests for the weekly reminder job."""
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.db import database
from bot.lexicon import Lexicon
from bot.utils import schedulers


class FakeBot:
    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail or {}

    async def send_message(self, chat_id, text, **kwargs):
        error = self.fail.get(chat_id)
        if error is not None:
            # Raise once, then succeed, so retry paths are observable.
            self.fail[chat_id] = None
            raise error
        self.sent.append((chat_id, text))


def volunteer(volunteer_id, name, tg_id):
    return SimpleNamespace(id=volunteer_id, full_name=name, tg_id=tg_id)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(schedulers.asyncio, "sleep", lambda _: _noop())
    monkeypatch.setattr(Lexicon, "REMINDER1", "Привіт, {}! Заповни статистику.", raising=False)
    # The reminder attaches the main menu keyboard, which reads these.
    monkeypatch.setattr(Lexicon, "FILL_STATISTICS", "Заповнити", raising=False)
    monkeypatch.setattr(Lexicon, "SELECT_PREVIOUS_WEEK", "Інший тиждень", raising=False)
    monkeypatch.setattr(Lexicon, "GET_MY_STATS", "Моя статистика", raising=False)
    monkeypatch.setattr(Lexicon, "START_DATE", "01.01.2026", raising=False)
    monkeypatch.setattr(Lexicon, "END_DATE", "31.12.2026", raising=False)
    monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "4", raising=False)
    monkeypatch.setattr(schedulers.config, "ADMIN_IDS", (4242,), raising=False)


async def _noop():
    return None


@pytest.fixture
def roster(monkeypatch):
    """Sheet says these three have not filled anything in."""
    people = {
        "Перший Волонтер": volunteer(1, "Перший Волонтер", 111),
        "Другий Волонтер": volunteer(2, "Другий Волонтер", 222),
        "Незареєстрований Волонтер": volunteer(3, "Незареєстрований Волонтер", None),
    }

    async def fetch(week):
        return [*people, "Привид Якийсь"]  # last one is not in the database

    async def by_name(name):
        return people.get(name)

    monkeypatch.setattr(schedulers, "fetch_users_with_no_stats", fetch)
    monkeypatch.setattr(database, "get_user_by_full_name", by_name)
    return people


class TestResolveRecipients:
    async def test_skips_names_absent_from_the_database(self, roster):
        """The sheet can name someone the database does not know; this used to
        raise AttributeError on None and abort the whole job."""
        recipients = await schedulers.resolve_recipients(1)
        assert "Привид Якийсь" not in [u.full_name for u in recipients]

    async def test_skips_volunteers_with_no_telegram_account(self, roster):
        """send_message(None, ...) would otherwise be attempted."""
        recipients = await schedulers.resolve_recipients(1)
        assert all(u.tg_id is not None for u in recipients)

    async def test_returns_the_registered_unfilled_volunteers(self, roster):
        recipients = await schedulers.resolve_recipients(1)
        assert [u.tg_id for u in recipients] == [111, 222]

    async def test_explicit_ids_are_resolved_individually(self, monkeypatch, roster):
        async def by_tg_id(tg_id):
            return volunteer(9, "Адмін Тест", tg_id) if tg_id == 555 else None

        monkeypatch.setattr(database, "get_user_by_tg_id", by_tg_id)
        recipients = await schedulers.resolve_recipients(1, user_ids=[555, 777])
        assert [u.tg_id for u in recipients] == [555]


class TestSendWeeklyReminder:
    async def test_sends_one_message_per_recipient_plus_one_admin_summary(self, roster):
        """The admin used to get a direct message for every volunteer
        reminded, three times over -- once per escalation level."""
        bot = FakeBot()
        await schedulers.send_weekly_reminder(bot, 1)

        recipients = [chat for chat, _ in bot.sent if chat != 4242]
        summaries = [text for chat, text in bot.sent if chat == 4242]
        assert recipients == [111, 222]
        assert len(summaries) == 1

    async def test_the_reminder_is_personalised(self, roster):
        bot = FakeBot()
        await schedulers.send_weekly_reminder(bot, 1)
        _, text = bot.sent[0]
        assert "Волонтер" in text

    async def test_a_name_with_html_in_it_is_escaped(self, monkeypatch, roster):
        """Reminders are the one volunteer-facing message sent with
        parse_mode="HTML", because REMINDER3 links to the spreadsheet. An
        unescaped '&' in a roster name would make Telegram refuse the whole
        message, so that one person would silently never be reminded."""
        monkeypatch.setattr(
            schedulers, "display_name", lambda _: "Ivan & Co <test>"
        )
        bot = FakeBot()
        await schedulers.send_weekly_reminder(bot, 1)

        _, text = bot.sent[0]
        assert "&amp;" in text
        assert "&lt;test&gt;" in text
        assert "& Co <test>" not in text

    async def test_a_blocked_user_is_reported_not_raised(self, roster):
        bot = FakeBot(fail={111: TelegramForbiddenError(method=None, message="blocked")})
        await schedulers.send_weekly_reminder(bot, 1)

        summary = next(text for chat, text in bot.sent if chat == 4242)
        assert "1 sent, 1 failed" in summary
        assert "blocked the bot" in summary

    async def test_rate_limiting_retries_the_actual_reminder(self, roster):
        """The retry path used to send the literal string "..." instead of the
        reminder, so the volunteer never received it."""
        bot = FakeBot(fail={111: TelegramRetryAfter(method=None, message="flood", retry_after=0)})
        await schedulers.send_weekly_reminder(bot, 1)

        to_111 = [text for chat, text in bot.sent if chat == 111]
        assert len(to_111) == 1
        assert "..." not in to_111[0]

    async def test_nothing_is_sent_outside_the_semester(self, monkeypatch, roster):
        monkeypatch.setattr(Lexicon, "START_DATE", "01.01.2030", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", "31.12.2030", raising=False)
        bot = FakeBot()
        await schedulers.send_weekly_reminder(bot, 1)
        assert bot.sent == []


class TestAdminSummary:
    def test_counts_both_outcomes(self):
        text = schedulers.format_admin_summary(2, ["А", "Б"], ["В: blocked the bot"])
        assert "Reminder level 2: 2 sent, 1 failed" in text
        assert "В: blocked the bot" in text

    def test_omits_empty_sections(self):
        assert "Failed" not in schedulers.format_admin_summary(1, ["А"], [])
        assert "Sent" not in schedulers.format_admin_summary(1, [], ["Б: oops"])
