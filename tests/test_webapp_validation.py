"""What the web UI refuses to save.

The grading matters as much as the detection. An error blocks the save, so it
has to be reserved for things that are certainly wrong; a tool that blocks
something merely unusual gets worked around, and the way around a config UI is
editing the database by hand.
"""
import pytest

from typing import ClassVar

from bot import registry
from bot.webapp import validation
from bot.webapp.validation import blocking

REMINDER = registry.BY_KEY["REMINDER1"]
QUESTION = registry.BY_KEY["QUESTION_1"]
SHORT_NAME = registry.BY_KEY["QUESTION_1_NAME"]
START = registry.BY_KEY["START_DATE"]
END = registry.BY_KEY["END_DATE"]
PATTERN = registry.BY_KEY["QUESTION_PATTERN"]
WORKSHEET = registry.BY_KEY["WORKSHEET_NAME"]


def errors(problems) -> list[str]:
    return [p.message for p in problems if p.blocking]


def warnings(problems) -> list[str]:
    return [p.message for p in problems if not p.blocking]


class TestPlaceholders:
    def test_the_declared_placeholder_is_accepted(self):
        assert not blocking(validation.check_placeholders(REMINDER, "Привіт, {0}!"))

    def test_a_placeholder_the_bot_does_not_fill_is_an_error(self):
        """fill() catches the IndexError and sends the template unsubstituted,
        so the volunteer is shown a literal {1}."""
        problems = validation.check_placeholders(REMINDER, "Привіт, {0} і {1}")
        assert any("{1}" in m for m in errors(problems))

    def test_a_named_placeholder_is_an_error(self):
        problems = validation.check_placeholders(REMINDER, "Привіт, {name}")
        assert any("{name}" in m for m in errors(problems))

    def test_a_missing_declared_placeholder_is_only_a_warning(self):
        """Five of the eight live reminder variants leave the name out on
        purpose, and str.format simply ignores the unused argument."""
        problems = validation.check_placeholders(REMINDER, "Час заповнити статистику")
        assert errors(problems) == []
        assert warnings(problems)

    def test_an_unbalanced_brace_is_an_error(self):
        assert blocking(validation.check_placeholders(REMINDER, "Привіт, {0"))

    def test_a_doubled_brace_is_a_literal_and_not_reported(self):
        assert validation.check_placeholders(REMINDER, "Привіт {{дужка}} {0}") == [] or (
            errors(validation.check_placeholders(REMINDER, "Привіт {{дужка}} {0}")) == []
        )

    def test_a_message_with_no_placeholders_declared_rejects_any(self):
        problems = validation.check_placeholders(QUESTION, "Скільки розмов? {0}")
        assert any("no values at all" in m for m in errors(problems))


class TestTelegramHtml:
    def test_the_real_reminder_link_passes(self):
        """REMINDER3 carries an <a href> to the spreadsheet in production."""
        live = (
            "Уже вечір, остання можливість заповнити статистику. Якщо не можеш "
            "зробити це зараз - заповни вручну в "
            "<a href='https://docs.google.com/spreadsheets/d/abc'>таблиці</a>"
        )
        assert errors(validation.check_telegram_html(live)) == []

    def test_plain_text_passes(self):
        assert validation.check_telegram_html("Просто текст без розмітки") == []

    def test_a_bare_less_than_is_an_error(self):
        """Telegram refuses the whole message, so the reminder reaches nobody
        and the only trace is a line in the log."""
        problems = validation.check_telegram_html("Треба більше 5 < 10 розмов")
        assert any("&lt;" in m for m in errors(problems))

    def test_an_unknown_tag_is_an_error(self):
        assert any("<marquee>" in m for m in errors(
            validation.check_telegram_html("<marquee>текст</marquee>")
        ))

    def test_an_unclosed_tag_is_an_error(self):
        assert any("never closed" in m for m in errors(
            validation.check_telegram_html("<b>жирний текст")
        ))

    def test_a_stray_closing_tag_is_an_error(self):
        assert any("never opened" in m for m in errors(
            validation.check_telegram_html("текст</b>")
        ))

    def test_tags_closed_out_of_order_are_an_error(self):
        assert any("out of order" in m for m in errors(
            validation.check_telegram_html("<b><i>текст</b></i>")
        ))

    def test_properly_nested_tags_pass(self):
        assert errors(validation.check_telegram_html("<b><i>текст</i></b>")) == []

    def test_a_bare_ampersand_is_only_a_warning(self):
        problems = validation.check_telegram_html("Статистика & звіти")
        assert errors(problems) == []
        assert warnings(problems)

    def test_an_entity_is_not_flagged(self):
        assert validation.check_telegram_html("5 &lt; 10 &amp; більше") == []

    def test_html_is_only_checked_where_it_is_parsed(self):
        """Volunteer-facing handlers send plain text, so a '<' there is just a
        character and refusing it would be wrong."""
        assert not QUESTION.html
        assert errors(validation.check_variants(QUESTION, ["Скільки розмов < 5?"])) == []
        assert REMINDER.html


class TestLengths:
    def test_a_short_name_over_its_limit_is_refused(self):
        problems = validation.check_variants(SHORT_NAME, ["я" * 41])
        assert any("limit here is 40" in m for m in errors(problems))

    def test_a_short_name_at_its_limit_passes(self):
        assert errors(validation.check_variants(SHORT_NAME, ["я" * 40])) == []

    def test_a_message_over_telegram_s_limit_is_refused(self):
        problems = validation.check_variants(QUESTION, ["я" * 4097])
        assert any("refuses a longer message" in m for m in errors(problems))


class TestEmptiness:
    def test_a_required_key_cannot_be_emptied(self):
        assert blocking(validation.check_variants(START, []))

    def test_whitespace_counts_as_empty(self):
        assert blocking(validation.check_variants(START, ["   ", ""]))


class TestSingleValueKinds:
    @pytest.mark.parametrize("field", [START, PATTERN, WORKSHEET])
    def test_a_parsed_key_takes_one_phrasing(self, field):
        problems = validation.check_variants(field, ["01.09.2026", "02.09.2026"])
        assert any("exactly one phrasing" in m for m in errors(problems))

    def test_a_message_may_have_several(self):
        assert errors(validation.check_variants(QUESTION, ["Скільки?", "А скільки?"])) == []


class TestDates:
    def test_a_well_formed_date_passes(self):
        assert errors(validation.check_variants(START, ["07.09.2026"])) == []

    @pytest.mark.parametrize("bad", ["2026-09-07", "7 вересня", "32.01.2026", ""])
    def test_a_malformed_date_is_refused(self, bad):
        assert blocking(validation.check_variants(START, [bad]))

    def test_an_end_before_the_start_is_refused(self):
        problems = validation.check_variants(
            END, ["01.09.2026"], other_date="30.12.2026"
        )
        assert any("end before it starts" in m for m in errors(problems))

    def test_a_semester_spanning_new_year_is_refused(self):
        """Week arithmetic uses bare ISO week numbers, so this makes the bot
        permanently unavailable rather than failing loudly."""
        problems = validation.check_variants(
            END, ["15.02.2027"], other_date="07.09.2026"
        )
        assert any("one calendar year" in m for m in errors(problems))

    def test_the_live_semester_passes(self):
        assert errors(
            validation.check_variants(START, ["07.09.2026"], other_date="30.12.2026")
        ) == []
        assert errors(
            validation.check_variants(END, ["30.12.2026"], other_date="07.09.2026")
        ) == []


class TestQuestionPattern:
    def test_the_live_pattern_passes(self):
        assert errors(validation.check_variants(PATTERN, ["4"])) == []

    def test_a_non_numeric_pattern_is_refused_with_an_example(self):
        problems = validation.check_variants(PATTERN, ["три-два"])
        assert any("3232" in m for m in errors(problems))

    def test_all_zeroes_is_refused(self):
        assert any("no week would ask anything" in m for m in errors(
            validation.check_variants(PATTERN, ["000"])
        ))

    def test_more_questions_than_the_bot_describes_is_refused(self):
        assert blocking(validation.check_variants(PATTERN, ["9"]))

    def test_changing_it_always_warns_about_the_columns(self):
        """The pattern decides which spreadsheet columns a week lands in, so a
        change silently relocates every subsequent week."""
        assert any("columns" in m for m in warnings(
            validation.check_variants(PATTERN, ["323"])
        ))


class TestButtonLabels:
    def test_a_label_that_belongs_to_another_button_is_refused(self):
        """LexiconFilter matches an incoming message against every label, so
        whichever router is registered first would answer both."""
        button = next(f for f in registry.FIELDS if f.kind is registry.Kind.BUTTON)
        other = next(
            f for f in registry.FIELDS
            if f.kind is registry.Kind.BUTTON and f.key != button.key
        )
        problems = validation.check_variants(
            button, ["Моя статистика"], other_labels={other.key: ["Моя статистика"]}
        )
        assert any(other.key in m for m in errors(problems))

    def test_its_own_current_label_is_not_a_clash(self):
        button = next(f for f in registry.FIELDS if f.kind is registry.Kind.BUTTON)
        problems = validation.check_variants(
            button, ["Моя статистика"], other_labels={button.key: ["Моя статистика"]}
        )
        assert errors(problems) == []

    def test_a_label_starting_with_a_slash_is_refused(self):
        """Commands are excluded from the answer handler, so the button would
        do nothing at all."""
        button = next(f for f in registry.FIELDS if f.kind is registry.Kind.BUTTON)
        assert any("command" in m for m in errors(
            validation.check_variants(button, ["/stats"])
        ))


class TestTheLiveConfigurationPasses:
    """A rule that refuses what production is already serving is a rule that
    stops anyone editing anything. These are the real values, read off the
    server on 2026-11-28."""

    LIVE: ClassVar[dict[str, list[str]]] = {
        "REMINDER1": [
            "Привіт, {0}, настав час заповнити статистику за тиждень",
            "Хай 👋 сьогодні неділя а це значить що треба заповнити статистику",
        ],
        "REMINDER2": [
            "Бачу, ти все ще не заповнив статистику. Зроби це зараз, це займе до хвилини часу",
            "Привіт, знову нагадую за заповнення статистики",
            "{0}, Заповни статистику, поки в тебе ще свіжа пам'ять. Бо потім забудеш, що на цьому тижні робилося",
        ],
        "REMINDER3": [
            "Уже вечір, остання можливість заповнити статистику. Якщо не можеш "
            "зробити це зараз - заповни вручну в "
            "<a href='https://docs.google.com/spreadsheets/d/x/edit'>таблиці</a>",
            "Востаннє нагадую за заповнення статистики. Це не так важко",
            "{0}, Ну заповни ти ту статистику. Скільки тебе просити?",
        ],
    }

    @pytest.mark.parametrize("key", list(LIVE))
    def test_every_live_reminder_would_still_be_savable(self, key):
        problems = validation.check_variants(registry.BY_KEY[key], self.LIVE[key])
        assert errors(problems) == [], errors(problems)
