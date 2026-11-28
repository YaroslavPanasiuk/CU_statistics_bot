"""The one-shot import of the config spreadsheet into Postgres.

The planning half is pure and tested directly; the writing half is tested
against a real Postgres, because the thing that matters is whether the bot can
load back what the import wrote.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import migrate_config

from bot import registry


def described(n: int = 3) -> list[str]:
    """A few keys the registry actually describes."""
    return [f.key for f in registry.FIELDS if f.group is not registry.Group.UNUSED][:n]


class TestPlanning:
    def test_a_key_the_store_lacks_is_new(self):
        key = described(1)[0]
        result = migrate_config.plan({key: ["текст"]}, {})
        assert result["new"] == {key: ["текст"]}
        assert result["changed"] == {}

    def test_an_identical_key_is_unchanged(self):
        key = described(1)[0]
        result = migrate_config.plan({key: ["текст"]}, {key: ["текст"]})
        assert result["unchanged"] == [key]
        assert result["new"] == {}

    def test_a_different_value_is_a_change_carrying_both_sides(self):
        key = described(1)[0]
        result = migrate_config.plan({key: ["нове"]}, {key: ["старе"]})
        assert result["changed"] == {key: (["старе"], ["нове"])}

    def test_reordered_variants_count_as_a_change(self):
        """Order is what select_random_line walks and what the UI shows."""
        key = described(1)[0]
        result = migrate_config.plan({key: ["б", "а"]}, {key: ["а", "б"]})
        assert key in result["changed"]

    def test_a_key_the_registry_does_not_describe_is_skipped(self):
        """The web UI only shows described keys, so an undescribed one would
        sit in the database invisible and undeletable."""
        result = migrate_config.plan({"SOME_OLD_KEY": ["текст"]}, {})
        assert result["undescribed"] == ["SOME_OLD_KEY"]
        assert result["new"] == {}

    def test_a_key_only_the_database_has_is_left_alone(self):
        """This adds the sheet's contents; it does not make the database a
        mirror, or a key added in the web UI would vanish on the next import."""
        key = described(1)[0]
        result = migrate_config.plan({}, {key: ["редаговано"]})
        assert result["only_stored"] == [key]
        assert result["changed"] == {}

    def test_only_described_keys_are_offered_for_import(self):
        keys = described(2)
        mapping = {keys[0]: ["a"], keys[1]: ["b"], "UNKNOWN_KEY": ["c"]}
        result = migrate_config.plan(mapping, {})
        assert set(migrate_config.to_import(result, mapping)) == set(keys)


class TestTheReport:
    def test_it_names_every_kind_of_difference(self):
        keys = described(3)
        mapping = {
            keys[0]: ["нове значення"],
            keys[1]: ["змінене"],
            keys[2]: ["те саме"],
            "UNKNOWN_KEY": ["щось"],
        }
        stored = {keys[1]: ["старе"], keys[2]: ["те саме"], "EXTRA": ["лишнє"]}
        text = migrate_config.render(migrate_config.plan(mapping, stored), mapping)

        assert f"+ {keys[0]}" in text
        assert f"~ {keys[1]}" in text
        assert "старе" in text and "змінене" in text
        assert "already match" in text
        assert "UNKNOWN_KEY" in text and "SKIPPED" in text
        assert "EXTRA" in text

    def test_a_long_value_is_shortened_and_newlines_shown(self):
        line = migrate_config.excerpt("перший рядок\nдругий рядок " + "х" * 200)
        assert "⏎" in line
        assert len(line) <= migrate_config.EXCERPT

    def test_a_short_value_is_untouched(self):
        assert migrate_config.excerpt("коротко") == "коротко"


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)
class TestImporting:
    @pytest.fixture
    async def clean(self):
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

    @pytest.fixture
    def sheet(self, monkeypatch):
        """A spreadsheet the bot would start on."""
        mapping = {key: ["текст"] for key in registry.REQUIRED_KEYS}
        mapping.update(
            {
                "START_DATE": ["07.09.2026"],
                "END_DATE": ["30.12.2026"],
                "QUESTION_PATTERN": ["2"],
                "QUESTION_1": ["Скільки розмов?"],
                "QUESTION_1_NAME": ["Розмови"],
                "QUESTION_2": ["Скільки зустрічей?"],
                "QUESTION_2_NAME": ["Зустрічі"],
            }
        )
        monkeypatch.setattr(migrate_config.lexicon, "sheet_mapping", lambda: mapping)
        return mapping

    async def test_a_dry_run_writes_nothing(self, clean, sheet, capsys):
        from bot.db import configstore

        assert await migrate_config.run(write=False, force=False) == 0
        assert await configstore.load() == {}
        assert "Dry run" in capsys.readouterr().out

    async def test_writing_imports_the_sheet(self, clean, sheet):
        from bot.db import configstore

        assert await migrate_config.run(write=True, force=False) == 0
        stored = await configstore.load()
        assert stored["START_DATE"] == ["07.09.2026"]
        assert stored["QUESTION_1"] == ["Скільки розмов?"]

    async def test_the_bot_can_load_what_was_imported(self, clean, sheet):
        """The only question that matters. An import the bot refuses is an
        import that takes the bot down on its next restart."""
        from bot import lexicon

        await migrate_config.run(write=True, force=False)
        assert await lexicon.load_from_store() is True
        assert lexicon.source() == "database"
        lexicon.validate_config()

    async def test_running_twice_changes_nothing(self, clean, sheet):
        from bot.db import configstore

        await migrate_config.run(write=True, force=False)
        version = await configstore.version()

        assert await migrate_config.run(write=True, force=False) == 0
        assert await configstore.version() == version, "a no-op must not bump the version"

    async def test_a_spreadsheet_the_bot_would_refuse_is_not_written(
        self, clean, sheet, monkeypatch, capsys
    ):
        broken = dict(sheet)
        broken["START_DATE"] = ["не дата"]
        monkeypatch.setattr(migrate_config.lexicon, "sheet_mapping", lambda: broken)

        from bot.db import configstore

        assert await migrate_config.run(write=True, force=False) == 1
        assert await configstore.load() == {}
        assert "would refuse" in capsys.readouterr().out

    async def test_an_empty_spreadsheet_is_refused(self, clean, monkeypatch, capsys):
        """A Sheets fault that returns nothing must not empty the store, which
        is what would roll the whole deployment back to the sheet."""
        monkeypatch.setattr(migrate_config.lexicon, "sheet_mapping", lambda: {})
        assert await migrate_config.run(write=True, force=False) == 1
        assert "no keys at all" in capsys.readouterr().out

    async def test_web_ui_edits_are_not_silently_overwritten(
        self, clean, sheet, capsys
    ):
        """The sheet is frozen in time and the database is not. Re-running
        after the cutover would otherwise discard every edit made since."""
        from bot.db import configstore

        await migrate_config.run(write=True, force=False)
        await configstore.set_variants("QUESTION_1", ["Відредаговано в UI"], "@yaros")

        assert await migrate_config.run(write=True, force=False) == 1
        assert (await configstore.load())["QUESTION_1"] == ["Відредаговано в UI"]
        assert "REFUSED" in capsys.readouterr().out

    async def test_force_overwrites_them_deliberately(self, clean, sheet):
        from bot.db import configstore

        await migrate_config.run(write=True, force=False)
        await configstore.set_variants("QUESTION_1", ["Відредаговано в UI"], "@yaros")

        assert await migrate_config.run(write=True, force=True) == 0
        assert (await configstore.load())["QUESTION_1"] == ["Скільки розмов?"]

    async def test_the_import_is_attributed_in_the_audit(self, clean, sheet):
        from bot.db import configstore

        await migrate_config.run(write=True, force=False)
        entries = await configstore.audit()
        assert entries
        assert entries[0].changed_by == "migrate_config.py"
