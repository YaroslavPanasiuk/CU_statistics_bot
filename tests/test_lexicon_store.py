"""The bot reading its configuration from the database instead of the sheet.

Covers the resolution order between the two sources, and the fail-safe
reload: a bad edit in the web UI must be rejected, not served.
"""
import os

import pytest

from bot import lexicon
from bot.lexicon import Lexicon, LexiconError

VALID = {
    "START_DATE": ["01.09.2025"],
    "END_DATE": ["21.12.2025"],
    "QUESTION_PATTERN": ["2"],
}


@pytest.fixture(autouse=True)
def clean_lexicon(monkeypatch):
    """Isolate the module-level cache; it is process-wide state."""
    monkeypatch.setattr(lexicon, "_variants", {}, raising=False)
    monkeypatch.setattr(lexicon, "_version", None, raising=False)
    monkeypatch.setattr(lexicon, "_rejected_version", None, raising=False)
    monkeypatch.setattr(lexicon, "_sheet_keys", set(), raising=False)


class TestResolution:
    def test_a_stored_key_is_readable_as_an_attribute(self):
        lexicon._variants["START_DATE"] = ["01.09.2025"]
        assert Lexicon.START_DATE == "01.09.2025"

    def test_an_absent_key_still_raises_attribute_error(self):
        """getattr(Lexicon, key, None) is used throughout; it must stay falsy."""
        with pytest.raises(AttributeError):
            Lexicon.NO_SUCH_KEY  # noqa: B018
        assert getattr(Lexicon, "NO_SUCH_KEY", None) is None

    def test_a_multi_variant_attribute_read_gives_the_first(self):
        lexicon._variants["WORKSHEET_NAME"] = ["Осінь", "Весна"]
        assert Lexicon.WORKSHEET_NAME == "Осінь"

    def test_stored_variants_are_all_offered(self):
        lexicon._variants["REMINDER1"] = ["а", "б", "в"]
        assert set(lexicon.variants("REMINDER1")) == {"а", "б", "в"}

    def test_a_sheet_value_still_splits_on_the_separator(self):
        """Both sources have to work through the cutover."""
        monkey = "а;;б"
        try:
            Lexicon.REMINDER1 = monkey
            assert lexicon.variants("REMINDER1") == ["а", "б"]
        finally:
            del Lexicon.REMINDER1

    def test_a_sheet_value_wins_over_a_stored_one(self):
        """Real class attributes shadow the cache, which is what keeps
        monkeypatch.setattr working in every other test file."""
        lexicon._variants["REMINDER1"] = ["stored"]
        try:
            Lexicon.REMINDER1 = "from the sheet"
            assert lexicon.variants("REMINDER1") == ["from the sheet"]
        finally:
            del Lexicon.REMINDER1

    def test_select_random_line_names_the_missing_key(self):
        with pytest.raises(LexiconError, match="GREETING"):
            lexicon.select_random_line("GREETING")

    def test_source_reports_the_spreadsheet_until_a_store_load(self):
        assert lexicon.source() == "spreadsheet"


class TestSingleValueKinds:
    """A second variant of a message is a feature; a second START_DATE is a
    coin toss over when the semester starts."""

    def _mapping(self, **overrides):
        from bot import registry

        mapping = {key: ["значення"] for key in lexicon.REQUIRED_KEYS}
        mapping.update(VALID)
        for n in (1, 2):
            mapping[f"QUESTION_{n}"] = [f"Питання {n}?"]
            mapping[f"QUESTION_{n}_NAME"] = [f"Назва {n}"]
        assert registry.BY_KEY  # the registry is what names the kinds
        mapping.update(overrides)
        return mapping

    def test_a_valid_mapping_passes(self):
        lexicon.validate_mapping(self._mapping())

    @pytest.mark.parametrize("key", ["START_DATE", "QUESTION_PATTERN", "WORKSHEET_NAME"])
    def test_two_variants_of_a_parsed_key_are_rejected(self, key):
        mapping = self._mapping(**{key: ["одне", "інше"]})
        with pytest.raises(LexiconError, match=key):
            lexicon.validate_mapping(mapping)

    def test_two_variants_of_a_message_are_fine(self):
        lexicon.validate_mapping(self._mapping(SELECT_WEEK=["Обери тиждень:", "Який тиждень?"]))

    def test_a_missing_required_key_is_named(self):
        mapping = self._mapping()
        del mapping["START_DATE"]
        with pytest.raises(LexiconError, match="START_DATE"):
            lexicon.validate_mapping(mapping)

    def test_validation_does_not_touch_the_live_configuration(self):
        """validate_mapping runs against a candidate while the bot is still
        serving the previous one."""
        lexicon._variants["START_DATE"] = ["01.09.2025"]
        with pytest.raises(LexiconError):
            lexicon.validate_mapping({})
        assert Lexicon.START_DATE == "01.09.2025"


pytestmark_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)


@pytestmark_db
class TestReloadFromDatabase:
    @pytest.fixture(autouse=True)
    async def store(self):
        from sqlalchemy import text as sql

        from bot.db import database

        await database.init_db()
        async with database.Session() as session:
            await session.execute(
                sql("TRUNCATE settings, setting_variants, config_meta, config_audit CASCADE")
            )
            await session.commit()
        yield
        await database.dispose_db()

    async def _populate(self, **overrides):
        from bot.db import configstore

        mapping = {key: ["значення"] for key in lexicon.REQUIRED_KEYS}
        mapping.update(VALID)
        for n in (1, 2):
            mapping[f"QUESTION_{n}"] = [f"Питання {n}?"]
            mapping[f"QUESTION_{n}_NAME"] = [f"Назва {n}"]
        mapping.update(overrides)
        await configstore.import_mapping(mapping, "test")
        return mapping

    async def test_an_empty_store_falls_through_to_the_sheet(self):
        """This is what lets a deployment run before migrate_config.py, and
        what makes rolling back as simple as emptying the tables."""
        assert await lexicon.load_from_store() is False
        assert lexicon.source() == "spreadsheet"

    async def test_a_populated_store_is_loaded(self):
        await self._populate()
        assert await lexicon.load_from_store() is True
        assert Lexicon.START_DATE == "01.09.2025"
        assert lexicon.source() == "database"

    async def test_a_stored_variant_list_arrives_whole(self):
        await self._populate(SELECT_WEEK=["перший", "другий"])
        await lexicon.load_from_store()
        assert lexicon.variants("SELECT_WEEK") == ["перший", "другий"]

    async def test_loading_clears_keys_the_sheet_had_set(self):
        """A value deleted in the web UI must stop being served, and a class
        attribute would shadow the database copy forever."""
        Lexicon.SELECT_WEEK = "із таблиці"
        lexicon._sheet_keys.add("SELECT_WEEK")
        await self._populate(SELECT_WEEK=["із бази"])

        await lexicon.load_from_store()
        assert lexicon.variants("SELECT_WEEK") == ["із бази"]

    async def test_a_poll_with_no_edit_reports_nothing(self):
        await self._populate()
        await lexicon.load_from_store()
        assert await lexicon.refresh_if_stale() is False

    async def test_an_edit_is_picked_up(self):
        from bot.db import configstore

        await self._populate()
        await lexicon.load_from_store()

        await configstore.set_variants("SELECT_WEEK", ["відредаговано"], "tester")
        assert await lexicon.refresh_if_stale() is True
        assert lexicon.variants("SELECT_WEEK") == ["відредаговано"]

    async def test_a_first_load_is_not_reported_as_an_edit(self):
        await self._populate()
        assert await lexicon.refresh_if_stale() is False
        assert lexicon.source() == "database"

    async def test_a_broken_edit_is_rejected_and_the_old_one_kept(self):
        """The fail-safe. Saving a nonsense date in the web UI must not take
        the bot down; it ran 3786 restarts the one time validation could only
        report a problem by exiting."""
        from bot.db import configstore

        await self._populate()
        await lexicon.load_from_store()

        await configstore.set_variants("START_DATE", ["не дата"], "tester")
        assert await lexicon.refresh_if_stale() is False
        assert Lexicon.START_DATE == "01.09.2025"

    async def test_a_rejected_version_is_not_retried_every_poll(self):
        from bot.db import configstore

        await self._populate()
        await lexicon.load_from_store()
        await configstore.set_variants("START_DATE", ["не дата"], "tester")

        await lexicon.refresh_if_stale()
        rejected = lexicon._rejected_version
        assert rejected is not None
        assert await lexicon.refresh_if_stale() is False
        assert lexicon._rejected_version == rejected

    async def test_a_fixed_edit_after_a_rejected_one_is_picked_up(self):
        """Otherwise the only way out of a bad save would be a restart."""
        from bot.db import configstore

        await self._populate()
        await lexicon.load_from_store()

        await configstore.set_variants("START_DATE", ["не дата"], "tester")
        assert await lexicon.refresh_if_stale() is False

        await configstore.set_variants("START_DATE", ["02.09.2025"], "tester")
        assert await lexicon.refresh_if_stale() is True
        assert Lexicon.START_DATE == "02.09.2025"

    async def test_a_poll_survives_a_database_failure(self):
        """A hiccup should delay picking up edits, not end the loop and leave
        the bot unable to ever see another one."""
        import asyncio

        async def boom():
            raise RuntimeError("connection reset")

        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                await boom()
            return False

        original = lexicon.refresh_if_stale
        lexicon.refresh_if_stale = flaky
        try:
            task = asyncio.create_task(lexicon.poll_for_changes(interval=0.01))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            lexicon.refresh_if_stale = original

        assert len(calls) > 1, "the loop stopped at the first failure"
