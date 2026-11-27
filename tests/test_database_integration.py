"""Integration tests against a real Postgres.

Skipped unless TEST_DATABASE_URL is set, e.g.

    docker run -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=cutest \
        -p 55432:5432 postgres:17
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/cutest \
        .venv/bin/python -m pytest tests/test_database_integration.py

These cover the things that cannot be asserted without a database: the
registration race, and that unregistering a volunteer keeps their statistics.
"""
import asyncio
import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)

from bot.db import database  # noqa: E402  (import after the skip marker)


@pytest.fixture(scope="module", autouse=True)
async def schema():
    await database.init_db()
    yield
    await database.dispose_db()


@pytest.fixture(autouse=True)
async def clean():
    async with database.Session() as session:
        await session.execute(text("TRUNCATE statistics, volunteers RESTART IDENTITY CASCADE"))
        await session.commit()


async def make_volunteer(name="Волонтер Тест"):
    await database.sync_volunteers([name])
    return await database.get_user_by_full_name(name)


class TestRegistrationRace:
    async def test_only_one_of_two_concurrent_registrations_wins(self):
        """Regression: register_user read `tg_id is None` and wrote in separate
        transactions, so two people tapping the same name both "succeeded" and
        the later write silently replaced the earlier registration."""
        volunteer = await make_volunteer()

        results = await asyncio.gather(
            database.register_user(volunteer.id, 111111),
            database.register_user(volunteer.id, 222222),
        )

        assert sorted(results) == [False, True], results
        stored = await database.get_user_by_full_name(volunteer.full_name)
        assert stored.tg_id in (111111, 222222)

    async def test_registering_an_already_bound_volunteer_fails(self):
        volunteer = await make_volunteer()
        assert await database.register_user(volunteer.id, 111111) is True
        assert await database.register_user(volunteer.id, 222222) is False

    async def test_one_telegram_account_cannot_claim_two_volunteers(self):
        await database.sync_volunteers(["Перший Волонтер", "Другий Волонтер"])
        first = await database.get_user_by_full_name("Перший Волонтер")
        second = await database.get_user_by_full_name("Другий Волонтер")

        assert await database.register_user(first.id, 999) is True
        # Unique constraint on tg_id; must be reported, not raised.
        assert await database.register_user(second.id, 999) is False


class TestUnregisterKeepsStatistics:
    async def test_statistics_survive_unregistering(self):
        """The whole point of owning rows by volunteers.id.

        Under the old schema this either orphaned the rows (production, where
        the FK was never applied) or raised a foreign-key violation (any fresh
        deployment, where it was).
        """
        volunteer = await make_volunteer()
        await database.register_user(volunteer.id, 555)
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "4"})

        assert await database.unregister_user(555) is True

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert [r.week for r in rows] == [40]
        assert rows[0].stats == {"answer_1": "4"}

    async def test_re_registering_keeps_the_same_history(self):
        volunteer = await make_volunteer()
        await database.register_user(volunteer.id, 555)
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "4"})
        await database.unregister_user(555)

        # A different telegram account claims the same volunteer.
        assert await database.register_user(volunteer.id, 777) is True
        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert len(rows) == 1

    async def test_deleting_a_volunteer_cascades_to_statistics(self):
        volunteer = await make_volunteer()
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "1"})

        # sync_volunteers deletes anyone absent from the roster.
        await database.sync_volunteers(["Хтось Інший"])

        async with database.Session() as session:
            remaining = (await session.execute(text("SELECT count(*) FROM statistics"))).scalar_one()
        assert remaining == 0


class TestSaveUserStats:
    async def test_upserts_on_volunteer_and_week(self):
        volunteer = await make_volunteer()
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "1"})
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "9"})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert len(rows) == 1
        assert rows[0].stats == {"answer_1": "9"}

    async def test_distinct_weeks_are_separate_rows(self):
        volunteer = await make_volunteer()
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "1"})
        await database.save_user_stats(volunteer.id, week=41, data_dict={"answer_1": "2"})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert [r.week for r in rows] == [40, 41]

    async def test_week_range_filter(self):
        volunteer = await make_volunteer()
        for week in (37, 40, 45):
            await database.save_user_stats(volunteer.id, week=week, data_dict={"answer_1": "1"})

        rows = await database.get_all_statistics_for_user(volunteer.id, 38, 44)
        assert [r.week for r in rows] == [40]


class TestMergeUserStats:
    async def test_keeps_answers_for_keys_not_supplied(self):
        """The point of the merging upsert: a blank cell in the sheet must not
        erase an answer the volunteer submitted through the bot."""
        volunteer = await make_volunteer()
        await database.save_user_stats(
            volunteer.id, week=40, data_dict={"answer_1": "4", "answer_2": "7"}
        )

        # The sheet only has a value in the first column for this week.
        await database.merge_user_stats(volunteer.id, week=40, data_dict={"answer_1": "5"})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert rows[0].stats == {"answer_1": "5", "answer_2": "7"}

    async def test_an_empty_merge_changes_nothing(self):
        volunteer = await make_volunteer()
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "4"})
        await database.merge_user_stats(volunteer.id, week=40, data_dict={})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert rows[0].stats == {"answer_1": "4"}

    async def test_inserts_when_nothing_is_stored_yet(self):
        volunteer = await make_volunteer()
        await database.merge_user_stats(volunteer.id, week=41, data_dict={"answer_1": "2"})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert [r.week for r in rows] == [41]
        assert rows[0].stats == {"answer_1": "2"}

    async def test_save_user_stats_still_replaces_wholesale(self):
        """The questionnaire writes every answer for a week, so it must not
        leave stale keys behind when the question count changes."""
        volunteer = await make_volunteer()
        await database.save_user_stats(
            volunteer.id, week=40, data_dict={"answer_1": "4", "answer_2": "7"}
        )
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "1"})

        rows = await database.get_all_statistics_for_user(volunteer.id)
        assert rows[0].stats == {"answer_1": "1"}

    async def test_merge_bumps_last_modified(self):
        volunteer = await make_volunteer()
        await database.save_user_stats(volunteer.id, week=40, data_dict={"answer_1": "4"})
        before = (await database.get_all_statistics_for_user(volunteer.id))[0].last_modified

        await database.merge_user_stats(volunteer.id, week=40, data_dict={"answer_1": "5"})
        after = (await database.get_all_statistics_for_user(volunteer.id))[0].last_modified
        assert after >= before
