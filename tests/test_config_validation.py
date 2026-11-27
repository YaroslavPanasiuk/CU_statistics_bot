"""Startup validation.

Every case here previously produced a confusing runtime failure instead of a
clear refusal: a missing config key raised AttributeError from inside a
handler, a bad ADMIN_IDS raised ValueError on every incoming message, and a
semester spanning New Year made the bot silently declare itself permanently
unavailable.
"""
import pytest

from bot.config import ConfigError, parse_admin_ids
from bot.lexicon import REQUIRED_KEYS, Lexicon, LexiconError, fill, validate_config


class TestParseAdminIds:
    def test_preserves_order(self):
        """The first entry receives the scheduler reports."""
        assert parse_admin_ids("999,111,222") == (999, 111, 222)

    @pytest.mark.parametrize("raw", [" 1 , 2 ", "1,2,", ",1,2", "1,,2"])
    def test_tolerates_whitespace_and_stray_commas(self, raw):
        """IsAdmin re-split the raw string and called int() per message, so any
        of these raised ValueError for every single update."""
        assert parse_admin_ids(raw) == (1, 2)

    def test_deduplicates(self):
        assert parse_admin_ids("5,5,7") == (5, 7)

    @pytest.mark.parametrize("raw", ["", None])
    def test_empty_yields_no_admins(self, raw):
        assert parse_admin_ids(raw) == ()

    def test_rejects_a_non_numeric_entry_by_name(self):
        with pytest.raises(ConfigError, match="abc"):
            parse_admin_ids("1,abc")


@pytest.fixture
def valid_sheet(monkeypatch):
    """A config sheet that passes validation."""
    values = {
        "START_DATE": "07.09.2026",
        "END_DATE": "30.12.2026",
        "QUESTION_PATTERN": "4",
        "WORKSHEET_NAME": "Благовістя осінь 2026",
    }
    for key in REQUIRED_KEYS:
        monkeypatch.setattr(Lexicon, key, values.get(key, "текст"), raising=False)
    for n in range(1, 5):
        monkeypatch.setattr(Lexicon, f"QUESTION_{n}", f"Питання {n}?", raising=False)
        monkeypatch.setattr(Lexicon, f"QUESTION_{n}_NAME", f"Назва {n}", raising=False)
    return values


class TestValidateConfig:
    def test_accepts_the_real_production_config(self, valid_sheet):
        validate_config()

    @pytest.mark.parametrize("key", ["START_DATE", "QUESTION_PATTERN", "REMINDER2", "GET_MY_STATS"])
    def test_names_a_missing_key(self, valid_sheet, monkeypatch, key):
        monkeypatch.setattr(Lexicon, key, "", raising=False)
        with pytest.raises(LexiconError, match=key):
            validate_config()

    def test_lists_every_missing_key_at_once(self, valid_sheet, monkeypatch):
        monkeypatch.setattr(Lexicon, "REMINDER1", "", raising=False)
        monkeypatch.setattr(Lexicon, "REMINDER2", "", raising=False)
        with pytest.raises(LexiconError) as excinfo:
            validate_config()
        assert "REMINDER1" in str(excinfo.value) and "REMINDER2" in str(excinfo.value)

    def test_rejects_a_semester_spanning_new_year(self, valid_sheet, monkeypatch):
        """start_week 37, end_week 2 makes `start <= week <= end`
        unsatisfiable, so the bot would never consider itself available."""
        monkeypatch.setattr(Lexicon, "END_DATE", "10.01.2027", raising=False)
        with pytest.raises(LexiconError, match="one calendar year"):
            validate_config()

    def test_rejects_an_end_date_before_the_start(self, valid_sheet, monkeypatch):
        monkeypatch.setattr(Lexicon, "END_DATE", "01.03.2026", raising=False)
        with pytest.raises(LexiconError, match="before START_DATE"):
            validate_config()

    @pytest.mark.parametrize("bad", ["2026-09-07", "7 вересня", "07/09/2026", "notadate"])
    def test_rejects_a_misformatted_date(self, valid_sheet, monkeypatch, bad):
        monkeypatch.setattr(Lexicon, "START_DATE", bad, raising=False)
        with pytest.raises(LexiconError, match=r"dd\.mm\.yyyy"):
            validate_config()

    @pytest.mark.parametrize("bad", ["4a", "x", "4,3", "4 3", "-4"])
    def test_rejects_a_non_numeric_question_pattern(self, valid_sheet, monkeypatch, bad):
        """int(pattern[i]) raised deep inside the week arithmetic."""
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", bad, raising=False)
        with pytest.raises(LexiconError, match=r"QUESTION_PATTERN"):
            validate_config()

    def test_rejects_an_empty_question_pattern(self, valid_sheet, monkeypatch):
        """`% len(pattern)` was a ZeroDivisionError."""
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "", raising=False)
        with pytest.raises(LexiconError):
            validate_config()

    def test_rejects_an_all_zero_pattern(self, valid_sheet, monkeypatch):
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "00", raising=False)
        with pytest.raises(LexiconError, match="zeroes"):
            validate_config()

    def test_requires_a_question_for_every_slot_the_pattern_uses(self, valid_sheet, monkeypatch):
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "5", raising=False)
        with pytest.raises(LexiconError, match="QUESTION_5"):
            validate_config()

    def test_requires_a_display_name_for_every_question(self, valid_sheet, monkeypatch):
        """format_all_stats_for_user reads QUESTION_N_NAME and would raise
        while rendering a volunteer's own statistics."""
        monkeypatch.setattr(Lexicon, "QUESTION_4_NAME", "", raising=False)
        with pytest.raises(LexiconError, match="QUESTION_4_NAME"):
            validate_config()

    def test_a_shorter_pattern_needs_fewer_questions(self, valid_sheet, monkeypatch):
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "21", raising=False)
        monkeypatch.setattr(Lexicon, "QUESTION_4", "", raising=False)
        monkeypatch.setattr(Lexicon, "QUESTION_3", "", raising=False)
        validate_config()


class TestFill:
    def test_substitutes_normally(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "K", "Привіт, {}!", raising=False)
        assert fill("K", "Саша") == "Привіт, Саша!"

    def test_a_stray_brace_in_the_sheet_does_not_raise(self, monkeypatch):
        """A person typing "{" into the config sheet used to break the
        reminder for every recipient."""
        monkeypatch.setattr(Lexicon, "K", "Привіт, {} {oops}!", raising=False)
        assert "Привіт" in fill("K", "Саша")

    def test_a_template_with_no_placeholder_is_returned_as_is(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "K", "Без підстановки", raising=False)
        assert fill("K", "Саша") == "Без підстановки"


class TestOptionalStrings:
    """Strings with a built-in fallback.

    These are customisable from the config sheet, but a deployment that has
    not added the key must keep working -- validate_config only insists on
    keys the bot cannot operate without.
    """

    def test_falls_back_when_the_key_is_absent(self, monkeypatch):
        from bot.lexicon import text

        monkeypatch.delattr(Lexicon, "NOT_A_REAL_KEY", raising=False)
        assert text("NOT_A_REAL_KEY", "запасний варіант") == "запасний варіант"

    def test_falls_back_when_the_key_is_blank(self, monkeypatch):
        from bot.lexicon import text

        monkeypatch.setattr(Lexicon, "BLANK_KEY", "", raising=False)
        assert text("BLANK_KEY", "запасний варіант") == "запасний варіант"

    def test_the_sheet_wins_when_the_key_is_set(self, monkeypatch):
        from bot.lexicon import text

        monkeypatch.setattr(Lexicon, "CANCELLED", "Скасовано!", raising=False)
        assert text("CANCELLED", "default") == "Скасовано!"

    def test_supports_variants(self, monkeypatch):
        from bot.lexicon import text

        monkeypatch.setattr(Lexicon, "CANCELLED", "а;;б;;в", raising=False)
        assert {text("CANCELLED", "default") for _ in range(50)} <= {"а", "б", "в"}

    def test_fill_default_substitutes_into_the_fallback(self, monkeypatch):
        from bot.lexicon import fill_default

        monkeypatch.delattr(Lexicon, "GREETING", raising=False)
        assert fill_default("GREETING", "Привіт, {}!", "Саша") == "Привіт, Саша!"

    def test_fill_default_substitutes_into_the_sheet_value(self, monkeypatch):
        from bot.lexicon import fill_default

        monkeypatch.setattr(Lexicon, "GREETING", "Вітаю, {}!", raising=False)
        assert fill_default("GREETING", "Привіт, {}!", "Саша") == "Вітаю, Саша!"

    def test_fill_default_tolerates_a_stray_brace(self, monkeypatch):
        from bot.lexicon import fill_default

        monkeypatch.setattr(Lexicon, "GREETING", "Вітаю, {} {oops}", raising=False)
        assert "Вітаю" in fill_default("GREETING", "Привіт, {}!", "Саша")

    def test_none_of_these_keys_are_required(self):
        """Adding a fallback key to REQUIRED_KEYS would break every existing
        deployment on the next restart."""
        optional = {
            "NAME_NOT_RECOGNISED", "GREETING", "NOTHING_TO_CANCEL", "CANCELLED",
            "UNREGISTERED", "ANSWER_MUST_BE_A_NUMBER", "UNKNOWN_COMMAND",
            "SESSION_LOST", "NO_STATS_TEXT", "STATS_HEADER", "WEEK_NOT_FILLED",
            "WEEK_LABEL",
        }
        assert optional.isdisjoint(REQUIRED_KEYS)
