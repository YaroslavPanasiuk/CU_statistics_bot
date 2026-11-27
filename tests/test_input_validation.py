"""Validation of what users and admins type."""
import pytest

from bot.config import config
from bot.handlers.admin import reminder_level_or_error
from bot.handlers.common import parse_answer
from bot.lexicon import Lexicon
from bot.utils.formatters import format_all_stats_for_user, pad_label, question_label
from bot.db.database import WeekStats


class TestParseAnswer:
    @pytest.mark.parametrize(("raw", "expected"), [("4", "4"), ("0", "0"), (" 7 ", "7"), ("12", "12")])
    def test_accepts_counts(self, raw, expected):
        assert parse_answer(raw) == expected

    def test_normalises_leading_zeros(self):
        """So the sheet and the database agree on what was recorded."""
        assert parse_answer("007") == "7"

    @pytest.mark.parametrize("raw", ["не знаю", "", "   ", "-1", "3.5", "3,5", "/remind_admin", "один"])
    def test_rejects_anything_that_is_not_a_count(self, raw):
        """Unvalidated text was stored as-is and silently summed as 0, so a
        volunteer's totals were wrong with no indication."""
        assert parse_answer(raw) is None

    def test_rejects_non_ascii_digits(self):
        """str.isdigit() alone accepts superscripts and other digit forms,
        which int() may then reject or read unexpectedly."""
        assert parse_answer("٤") is None
        assert parse_answer("²") is None

    def test_rejects_a_non_text_message(self):
        """A sticker previously stored None as the answer."""
        assert parse_answer(None) is None

    def test_rejects_an_overlong_answer(self):
        assert parse_answer("1" * (config.MAX_ANSWER_LENGTH + 1)) is None

    def test_accepts_an_answer_at_the_limit(self):
        assert parse_answer("1" * config.MAX_ANSWER_LENGTH) is not None


class TestReminderLevelValidation:
    @pytest.fixture(autouse=True)
    def levels(self, monkeypatch):
        for n in (1, 2, 3):
            monkeypatch.setattr(Lexicon, f"REMINDER{n}", f"reminder {n}", raising=False)
        monkeypatch.delattr(Lexicon, "REMINDER9", raising=False)

    def test_accepts_a_configured_level(self):
        assert reminder_level_or_error("2", "usage") == (2, None)

    def test_rejects_a_level_with_no_config_key(self):
        """An out-of-range level used to raise inside the per-user send loop,
        so the admin got no feedback while every recipient failed."""
        level, error = reminder_level_or_error("9", "usage")
        assert level is None and "REMINDER9" in error

    @pytest.mark.parametrize("raw", ["abc", "2a", "-1", "2.5"])
    def test_rejects_a_non_numeric_level(self, raw):
        level, error = reminder_level_or_error(raw, "usage")
        assert level is None and "number" in error

    @pytest.mark.parametrize("raw", [None, ""])
    def test_missing_level_shows_usage(self, raw):
        level, error = reminder_level_or_error(raw, "/remind_admin [level]")
        assert level is None and "/remind_admin [level]" in error


class TestStatisticsRenderAsSafeHtml:
    @pytest.fixture(autouse=True)
    def names(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "QUESTION_1_NAME", "Розмови про Бога", raising=False)
        monkeypatch.setattr(Lexicon, "QUESTION_2_NAME", "Святе Письмо (вірші) - разом", raising=False)

    def row(self, stats):
        return WeekStats(full_name="Тест Волонтер", stats=stats, week=37)

    def test_a_name_with_markdown_punctuation_no_longer_breaks_the_message(self):
        """Sent as MarkdownV2 with nothing escaped, a name containing '.',
        '-', '(' or '!' made Telegram reject the message with a 400."""
        text = format_all_stats_for_user([self.row({"answer_2": "3"})])
        assert "Святе Письмо" in text
        assert "<code>" in text

    def test_html_special_characters_are_escaped(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "QUESTION_1_NAME", "Бесіди <b>& молитва</b>", raising=False)
        text = format_all_stats_for_user([self.row({"answer_1": "1"})])
        assert "&lt;b&gt;" in text and "&amp;" in text
        assert "<b>" not in text.replace("<code>", "").replace("</code>", "")

    def test_an_unknown_key_falls_back_to_the_key_name(self):
        """`key.split('_')[1]` used to raise on anything but answer_N."""
        assert question_label("selected_week") == "selected_week"
        assert question_label("weird") == "weird"

    def test_a_missing_question_name_does_not_raise(self, monkeypatch):
        monkeypatch.delattr(Lexicon, "QUESTION_7_NAME", raising=False)
        assert question_label("answer_7") == "answer_7"


class TestPadLabel:
    def test_short_labels_are_padded_to_a_fixed_width(self):
        assert len(pad_label("Коротко")) == 25

    def test_long_labels_are_truncated_to_the_same_width(self):
        """Truncation used to cut at 23 while the test was for more than 25,
        so the two paths produced different widths."""
        assert len(pad_label("Д" * 60)) == 25

    def test_a_label_at_the_boundary_is_not_truncated(self):
        assert "…" not in pad_label("Д" * 25)
