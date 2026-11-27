"""The registry and the code must not drift apart.

Every wrong message and every startup outage in this project's history came
from the same shape of mistake: a key the code expected that the sheet did not
have, or a key the code invented that already meant something else. These tests
are what makes bot/registry.py load-bearing rather than documentation.
"""
import re
from pathlib import Path
from typing import ClassVar

import pytest

from bot import registry
from bot.registry import BY_KEY, FIELDS, REQUIRED_KEYS, Group, Kind

BOT_DIR = Path(__file__).resolve().parents[1] / "bot"

#: How code reaches a config value.
READERS = re.compile(
    r"""(?:select_random_line|fill_default|fill|text|LexiconFilter)\(\s*['"]([A-Z][A-Z_0-9]*)['"]"""
)
#: Lexicon.SOME_KEY attribute access.
ATTRS = re.compile(r"Lexicon\.([A-Z][A-Z_0-9]*)")

#: Names on Lexicon that are not config keys.
NOT_KEYS = frozenset({"load_from_sheet", "START_WELCOME", "ASK_FULLNAME", "SUCCESS_ACTION"})


def keys_used_in_code() -> set[str]:
    used: set[str] = set()
    for path in BOT_DIR.rglob("*.py"):
        if path.name == "registry.py":
            continue
        source = path.read_text()
        used |= set(READERS.findall(source))
        used |= set(ATTRS.findall(source))
    return used - NOT_KEYS


class TestNoDrift:
    def test_every_key_the_code_reads_is_described(self):
        """A key the registry does not know is a key nobody can look up the
        meaning of -- and the class of bug that produced a twelve-hour outage."""
        undescribed = keys_used_in_code() - set(BY_KEY)
        assert not undescribed, (
            f"{sorted(undescribed)} are read by the code but not described in "
            "bot/registry.py. Add a Field for each."
        )

    def test_every_described_key_is_either_used_or_marked_unused(self):
        """Otherwise the config UI shows editors fields that change nothing."""
        used = keys_used_in_code()
        # Some keys are reached by a computed name -- f"REMINDER{level}", or
        # the question family by index -- so they never appear literally.
        dynamic = {f.key for f in FIELDS if f.conditional or f.dynamic}
        unused_group = {f.key for f in registry.fields_in_group(Group.UNUSED)}

        orphans = set(BY_KEY) - used - dynamic - unused_group
        assert not orphans, (
            f"{sorted(orphans)} are described but nothing reads them. Either "
            "use them, or move them to Group.UNUSED so the UI says so."
        )

    def test_the_reminder_family_declares_that_it_is_reached_by_index(self):
        """send_weekly_reminder builds f"REMINDER{level}", so the literal keys
        never appear in the source. The registry has to say so, or the orphan
        check above would demand they be marked unused."""
        for n in (1, 2, 3):
            assert BY_KEY[f"REMINDER{n}"].dynamic, f"REMINDER{n}"


class TestRequiredKeysExistInProduction:
    #: Verified present in the live config sheet on 2026-11-27.
    SHEET_KEYS: ClassVar[set[str]] = {
        "QUESTION_1", "QUESTION_2", "QUESTION_3", "QUESTION_4", "QUESTION_5",
        "QUESTION_1_NAME", "QUESTION_2_NAME", "QUESTION_3_NAME", "QUESTION_4_NAME",
        "GREETING", "ASK_NAME", "WHY_I_AM_HERE", "SEND_STATISTICS", "WHAT_I_DO",
        "STATISTICS_GATHERED", "YES", "NO", "FILL_STATISTICS", "MENU",
        "REGISTRATION_COMPLETE", "SEMESTER_NOT_STARTED", "SEMESTER_OVER",
        "NOT_REGISTERED", "WRITING", "REMINDER1", "REMINDER2", "REMINDER3",
        "VALIDATION_FAILED_NUMERIC", "VALIDATION_FAILED_NAME", "REMINDER_LATE",
        "REMINDER_SORRY", "SELECT_PREVIOUS_WEEK", "GETTING_YOUR_STATISTICS",
        "QUESTION_PATTERN", "START_DATE", "END_DATE", "SELECT_YOUR_NAME",
        "SELECT_WEEK", "SELECTED_WEEK", "WORKSHEET_NAME", "GET_MY_STATS",
        "BOT_UNAVAILABLE", "NO_STATS_AVAILABLE",
    }

    def test_nothing_required_is_absent_from_the_sheet(self):
        """Requiring a key the sheet lacks stops the bot from starting. That is
        exactly how NO_STATS_AVAILABLE caused a restart loop."""
        missing = set(REQUIRED_KEYS) - self.SHEET_KEYS
        assert not missing, (
            f"{sorted(missing)} are required but not known to be in the config "
            "sheet. Confirm with deploy/check_config.py, or set required=False "
            "and give the field a text() fallback."
        )

    def test_optional_keys_absent_from_the_sheet_are_genuinely_optional(self):
        """Every described key the sheet lacks must be required=False."""
        for key in set(BY_KEY) - self.SHEET_KEYS:
            field = BY_KEY[key]
            assert not field.required or field.conditional, (
                f"{key} is required but absent from the sheet"
            )

    def test_every_sheet_key_is_described(self):
        """So an editor is never shown a value with no explanation."""
        undescribed = self.SHEET_KEYS - set(BY_KEY)
        assert not undescribed, f"{sorted(undescribed)} are in the sheet but undescribed"


class TestFieldDefinitions:
    def test_keys_are_unique(self):
        keys = [f.key for f in FIELDS]
        assert len(keys) == len(set(keys))

    def test_every_field_has_a_label_and_help(self):
        """The whole point is that a reader can tell what a key does."""
        for f in FIELDS:
            assert f.label_uk.strip(), f.key
            assert len(f.help_uk.strip()) > 15, f"{f.key}: help text too thin"

    def test_templates_declare_their_placeholders(self):
        """A template whose placeholder is edited away breaks at send time."""
        for f in FIELDS:
            if f.placeholders:
                assert f.kind is Kind.TEMPLATE, f"{f.key} declares placeholders but is {f.kind}"

    def test_button_labels_are_limited_to_what_fits_a_phone(self):
        for f in FIELDS:
            if f.kind is Kind.BUTTON:
                assert f.limit == registry.BUTTON_LABEL_LIMIT, f.key

    def test_nothing_exceeds_the_telegram_message_limit(self):
        for f in FIELDS:
            assert f.limit <= registry.TELEGRAM_MESSAGE_LIMIT, f.key

    def test_the_three_menu_buttons_are_matched_against_incoming_text(self):
        """LexiconFilter compares a message against every ';;' variant, so all
        three must allow variants or adding one silently kills the button."""
        for key in ("FILL_STATISTICS", "SELECT_PREVIOUS_WEEK", "GET_MY_STATS"):
            assert BY_KEY[key].kind is Kind.BUTTON
            assert BY_KEY[key].variants, key

    def test_schedule_fields_are_not_free_text(self):
        for key, kind in (
            ("START_DATE", Kind.DATE),
            ("END_DATE", Kind.DATE),
            ("QUESTION_PATTERN", Kind.PATTERN),
            ("WORKSHEET_NAME", Kind.WORKSHEET),
        ):
            assert BY_KEY[key].kind is kind

    def test_greeting_and_greeting_registered_are_distinct_fields(self):
        """Collapsing them is what made /start reply with the whole intro."""
        assert BY_KEY["GREETING"].group is Group.UNUSED
        assert BY_KEY["GREETING_REGISTERED"].group is Group.REGISTRATION
        assert BY_KEY["GREETING_REGISTERED"].placeholders == ("{0}",)


class TestQuestionFamily:
    @pytest.mark.parametrize(
        ("pattern", "most"), [("4", 4), ("323", 3), ("1", 1), ("8", 8), ("21", 2)]
    )
    def test_the_schedule_decides_how_many_questions_are_needed(self, pattern, most):
        keys = registry.question_keys(pattern)
        assert f"QUESTION_{most}" in keys
        assert f"QUESTION_{most}_NAME" in keys
        assert f"QUESTION_{most + 1}" not in keys

    @pytest.mark.parametrize("pattern", ["", "4a", "x"])
    def test_an_invalid_schedule_needs_nothing(self, pattern):
        """validate_config rejects the pattern itself; this must not raise first."""
        assert registry.question_keys(pattern) == ()

    def test_questions_are_described_up_to_the_supported_maximum(self):
        for n in range(1, registry.MAX_QUESTIONS + 1):
            assert f"QUESTION_{n}" in BY_KEY
            assert f"QUESTION_{n}_NAME" in BY_KEY

    def test_question_fields_are_conditional_not_required(self):
        """Pattern "4" must not demand QUESTION_8."""
        for n in range(1, registry.MAX_QUESTIONS + 1):
            assert BY_KEY[f"QUESTION_{n}"].conditional
            assert f"QUESTION_{n}" not in REQUIRED_KEYS


class TestLexiconUsesTheRegistry:
    def test_lexicon_does_not_keep_its_own_required_list(self):
        """Two lists would drift, which is the problem being solved."""
        from bot import lexicon

        assert lexicon.REQUIRED_KEYS is REQUIRED_KEYS

    def test_variant_keys_cover_every_multi_variant_key_in_the_sheet(self):
        """The 13 keys that actually carry ';;' variants in production."""
        actual = {
            "WRITING", "QUESTION_1", "QUESTION_2", "QUESTION_3", "QUESTION_4",
            "STATISTICS_GATHERED", "REGISTRATION_COMPLETE", "REMINDER1",
            "REMINDER2", "REMINDER3", "BOT_UNAVAILABLE", "SEMESTER_NOT_STARTED",
            "GETTING_YOUR_STATISTICS",
        }
        assert actual <= registry.VARIANT_KEYS, (
            f"{sorted(actual - registry.VARIANT_KEYS)} hold ';;' variants in the "
            "sheet but are not marked variants=True, so the importer would keep "
            "them as one long string"
        )
