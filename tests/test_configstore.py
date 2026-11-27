"""Integration tests for the configuration store.

Skipped unless TEST_DATABASE_URL is set; see tests/test_database_integration.py
for how to point it at a throwaway Postgres.
"""
import json
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)

from bot.db import configstore, database  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
async def schema():
    await database.init_db()
    yield
    await database.dispose_db()


@pytest.fixture(autouse=True)
async def clean():
    async with database.Session() as session:
        await session.execute(
            text(
                "TRUNCATE settings, setting_variants, config_meta, config_audit, "
                "admins, admin_invites RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


class TestVariantsAreRows:
    async def test_a_single_value_round_trips(self):
        await configstore.set_variants("SELECT_WEEK", ["Обери тиждень:"], "tester")
        assert (await configstore.load())["SELECT_WEEK"] == ["Обери тиждень:"]

    async def test_variant_order_is_preserved(self):
        """Position matters: the UI shows them in order and reordering is an edit."""
        values = ["перший", "другий", "третій"]
        await configstore.set_variants("STATISTICS_GATHERED", values, "tester")
        assert (await configstore.load())["STATISTICS_GATHERED"] == values

    async def test_replacing_variants_does_not_leave_the_old_ones_behind(self):
        await configstore.set_variants("REMINDER1", ["a", "b", "c"], "tester")
        await configstore.set_variants("REMINDER1", ["x"], "tester")
        assert (await configstore.load())["REMINDER1"] == ["x"]

    async def test_blank_variants_are_dropped(self):
        await configstore.set_variants("REMINDER2", ["a", "   ", "", "b"], "tester")
        assert (await configstore.load())["REMINDER2"] == ["a", "b"]

    async def test_a_key_cannot_be_left_with_no_value(self):
        """A valueless key is indistinguishable from a missing one, and a
        missing required key stops the bot from starting."""
        with pytest.raises(ValueError, match="no value"):
            await configstore.set_variants("REMINDER3", ["  ", ""], "tester")

    async def test_values_keep_their_whitespace_meaning_but_are_trimmed(self):
        await configstore.set_variants("SELECTED_WEEK", ["  Обраний тиждень:  "], "tester")
        assert (await configstore.load())["SELECTED_WEEK"] == ["Обраний тиждень:"]

    async def test_deleting_a_key_removes_its_variants(self):
        await configstore.set_variants("WRITING", ["a", "b"], "tester")
        assert await configstore.delete_key("WRITING", "tester") is True
        assert "WRITING" not in await configstore.load()

    async def test_deleting_an_absent_key_reports_false(self):
        assert await configstore.delete_key("NOT_THERE", "tester") is False


class TestVersionCounter:
    async def test_starts_at_zero(self):
        assert await configstore.version() == 0

    async def test_every_write_bumps_it(self):
        await configstore.set_variants("SELECT_WEEK", ["a"], "tester")
        first = await configstore.version()
        await configstore.set_variants("SELECT_WEEK", ["b"], "tester")
        assert await configstore.version() == first + 1

    async def test_an_import_bumps_once_for_the_whole_batch(self):
        """Otherwise a 44-key import would make the bot reload 44 times."""
        await configstore.import_mapping(
            {f"KEY_{n}": [f"value {n}"] for n in range(10)}, "importer"
        )
        assert await configstore.version() == 1

    async def test_deleting_a_key_bumps_it(self):
        await configstore.set_variants("WRITING", ["a"], "tester")
        before = await configstore.version()
        await configstore.delete_key("WRITING", "tester")
        assert await configstore.version() == before + 1

    async def test_granting_an_admin_bumps_it(self):
        """IsAdmin is refreshed by the same poll, so a grant has to be visible."""
        before = await configstore.version()
        await configstore.add_admin(555, "someone", "Хтось", "tester")
        assert await configstore.version() == before + 1


class TestAudit:
    async def test_records_the_old_and_new_values(self):
        await configstore.set_variants("SELECT_WEEK", ["старе"], "перший")
        await configstore.set_variants("SELECT_WEEK", ["нове"], "другий")

        entries = await configstore.audit()
        latest = entries[0]
        assert latest.key == "SELECT_WEEK"
        assert latest.changed_by == "другий"
        assert json.loads(latest.old_value) == ["старе"]
        assert json.loads(latest.new_value) == ["нове"]

    async def test_a_first_write_has_no_old_value(self):
        await configstore.set_variants("SELECT_WEEK", ["нове"], "tester")
        assert (await configstore.audit())[0].old_value is None

    async def test_a_deletion_has_no_new_value(self):
        await configstore.set_variants("WRITING", ["a"], "tester")
        await configstore.delete_key("WRITING", "tester")
        assert (await configstore.audit())[0].new_value is None

    async def test_newest_first(self):
        for n in range(3):
            await configstore.set_variants(f"KEY_{n}", ["v"], "tester")
        keys = [e.key for e in await configstore.audit()]
        assert keys[:3] == ["KEY_2", "KEY_1", "KEY_0"]


class TestImport:
    async def test_is_idempotent(self):
        mapping = {"SELECT_WEEK": ["a"], "REMINDER1": ["b", "c"]}
        assert await configstore.import_mapping(mapping, "importer") == 2
        assert await configstore.import_mapping(mapping, "importer") == 2
        assert await configstore.load() == mapping

    async def test_skips_keys_with_nothing_in_them(self):
        written = await configstore.import_mapping(
            {"SELECT_WEEK": ["a"], "EMPTY": [], "BLANK": ["  "]}, "importer"
        )
        assert written == 1
        assert set(await configstore.load()) == {"SELECT_WEEK"}

    async def test_records_one_audit_entry_for_the_batch(self):
        await configstore.import_mapping({"A": ["1"], "B": ["2"]}, "importer")
        entries = await configstore.audit()
        assert len(entries) == 1
        assert entries[0].key == "(import)"


class TestAdmins:
    async def test_granting_and_listing(self):
        await configstore.add_admin(111, "@Someone", "Хтось", "tester")
        assert await configstore.admin_ids() == {111}
        assert (await configstore.admins())[0].username == "someone"

    async def test_usernames_are_normalised(self):
        """Telegram handles are case-insensitive and often pasted with an '@'."""
        assert configstore.normalise_username("  @SomeOne ") == "someone"

    async def test_revoking(self):
        await configstore.add_admin(111, "someone", "Хтось", "tester")
        assert await configstore.remove_admin(111, "tester") is True
        assert await configstore.admin_ids() == set()

    async def test_revoking_someone_absent_reports_false(self):
        assert await configstore.remove_admin(999, "tester") is False

    async def test_granting_twice_updates_rather_than_duplicating(self):
        await configstore.add_admin(111, "old_handle", "Старе", "tester")
        await configstore.add_admin(111, "new_handle", "Нове", "tester")
        rows = await configstore.admins()
        assert len(rows) == 1
        assert rows[0].username == "new_handle"

    async def test_env_admins_are_not_stored_here(self):
        """.env stays the way back in if this table is emptied by mistake."""
        assert await configstore.admin_ids() == set()


class TestAdminInvites:
    async def test_inviting_records_a_normalised_handle(self):
        assert await configstore.invite_admin("@SomeOne", "tester") == "someone"
        assert [i.username for i in await configstore.pending_invites()] == ["someone"]

    async def test_an_empty_username_is_rejected(self):
        with pytest.raises(ValueError, match="username is required"):
            await configstore.invite_admin("  @ ", "tester")

    async def test_signing_in_consumes_the_invite_and_grants_access(self):
        """The login payload is the only place a username and an id arrive
        together, because the Bot API cannot resolve one to the other."""
        await configstore.invite_admin("someone", "tester")
        assert await configstore.consume_invite("@SomeOne", 777, "Хтось") is True

        assert await configstore.admin_ids() == {777}
        assert await configstore.pending_invites() == []

    async def test_a_username_with_no_invite_is_not_granted_access(self):
        assert await configstore.consume_invite("stranger", 888, "Хтось") is False
        assert await configstore.admin_ids() == set()

    async def test_an_expired_invite_is_discarded_not_honoured(self):
        """Usernames can be released and re-registered, so a stale invite would
        eventually hand access to whoever claimed the handle."""
        await configstore.invite_admin("someone", "tester")
        async with database.Session() as session:
            await session.execute(
                text("UPDATE admin_invites SET expires_at = :past"),
                {"past": datetime.now().astimezone() - timedelta(days=1)},
            )
            await session.commit()

        assert await configstore.consume_invite("someone", 777, "Хтось") is False
        assert await configstore.admin_ids() == set()
        assert await configstore.pending_invites() == []

    async def test_an_invite_can_be_withdrawn(self):
        await configstore.invite_admin("someone", "tester")
        assert await configstore.revoke_invite("@someone", "tester") is True
        assert await configstore.pending_invites() == []

    async def test_the_granted_admin_keeps_who_invited_them(self):
        await configstore.invite_admin("someone", "інвайтер")
        await configstore.consume_invite("someone", 777, "Хтось")
        assert (await configstore.admins())[0].added_by == "інвайтер"

    async def test_identity_updates_keep_the_numeric_id(self):
        """The id is the identity; the username is only for display."""
        await configstore.add_admin(111, "old_handle", "Старе", "tester")
        await configstore.update_admin_identity(111, "@New_Handle", "Нове")
        rows = await configstore.admins()
        assert rows[0].tg_id == 111
        assert rows[0].username == "new_handle"
        assert rows[0].first_name == "Нове"
