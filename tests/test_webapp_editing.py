"""Reading and editing the configuration through the pages.

Driven against a real Postgres rather than a fake store: the fakes in
test_webapp_admins.py cannot catch a template that renders nothing, or a save
that writes a shape the bot cannot read back. Both have happened.
"""
import os
import time

import httpx
import pytest

from bot import registry
from bot.db import configstore
from bot.webapp import app as webapp
from bot.webapp import auth

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database integration tests",
)

TOKEN = "123456:test-token"
ADMIN_ID = 111

def _complete_config() -> dict[str, list[str]]:
    """A configuration the bot would actually start on.

    Every required key, because one of the tests loads this through
    lexicon.load_from_store() -- and a partial config is exactly what that
    refuses. Filling the gaps with placeholder text would have hidden the one
    assertion worth making.
    """
    mapping = {key: ["текст"] for key in registry.REQUIRED_KEYS}
    mapping.update(
        {
            "START_DATE": ["07.09.2026"],
            "END_DATE": ["30.12.2026"],
            "QUESTION_PATTERN": ["2"],
            "WORKSHEET_NAME": ["Осінь 2026"],
            "QUESTION_1": ["Скільки розмов?"],
            "QUESTION_1_NAME": ["Розмови"],
            "QUESTION_2": ["Скільки зустрічей?"],
            "QUESTION_2_NAME": ["Зустрічі"],
            "SELECT_WEEK": ["Обери тиждень:"],
            "REMINDER1": ["Привіт, {0}, час заповнити статистику"],
        }
    )
    # The templated keys need their placeholder or validation warns; the ones
    # the bot substitutes a name into are the only ones that matter here.
    for key in ("SEMESTER_NOT_STARTED", "SEMESTER_OVER", "REGISTRATION_COMPLETE"):
        mapping[key] = ["Привіт, {0}"]
    return mapping


BASE = _complete_config()


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth.config, "BOT_TOKEN", TOKEN, raising=False)
    monkeypatch.setattr(auth.config, "ADMIN_IDS", (ADMIN_ID,), raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("BOT_USERNAME", "cu_statistics_bot")


@pytest.fixture
async def store():
    from sqlalchemy import text as sql

    from bot.db import database

    await database.init_db()
    async with database.Session() as session:
        await session.execute(
            sql(
                "TRUNCATE settings, setting_variants, config_meta, config_audit, "
                "admins, admin_invites CASCADE"
            )
        )
        await session.commit()
    await configstore.import_mapping(BASE, "fixture")
    yield
    await database.dispose_db()


@pytest.fixture
async def client(store):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=webapp.app), base_url="https://testserver"
    ) as c:
        params = {
            "id": str(ADMIN_ID),
            "first_name": "Ярослав",
            "username": "yaros",
            "auth_date": str(int(time.time())),
        }
        params["hash"] = auth.sign_widget_payload(params, TOKEN)
        await c.get("/auth/telegram", params=params)
        yield c


async def csrf_from(client, path) -> str:
    page = (await client.get(path)).text
    marker = 'name="csrf" value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


class TestTheIndex:
    async def test_it_shows_what_the_bot_says(self, client):
        page = (await client.get("/")).text
        assert "Скільки розмов?" in page
        assert "Обери тиждень:" in page

    async def test_it_groups_by_what_the_key_is_for(self, client):
        page = (await client.get("/")).text
        assert registry.GROUP_LABELS_UK[registry.Group.SCHEDULE] in page
        assert registry.GROUP_LABELS_UK[registry.Group.QUESTIONS] in page

    async def test_unused_keys_are_not_offered(self, client):
        """Editing a key nothing reads is work that produces nothing."""
        page = (await client.get("/")).text
        for field in registry.FIELDS:
            if field.group is registry.Group.UNUSED:
                # The closing quote matters: /edit/GREETING is a substring of
                # /edit/GREETING_REGISTERED, which is a key that is offered.
                assert f'href="/edit/{field.key}"' not in page, field.key

    async def test_questions_the_schedule_never_asks_are_hidden(self, client):
        """The pattern is "2", so QUESTION_3 would only be clutter."""
        page = (await client.get("/")).text
        assert "/edit/QUESTION_2" in page
        assert "/edit/QUESTION_3" not in page

    async def test_a_key_with_no_value_is_called_out(self, client):
        await configstore.delete_key("SELECT_WEEK", "test")
        assert "Не задано" in (await client.get("/")).text

    async def test_several_phrasings_are_all_shown(self, client):
        await configstore.set_variants("SELECT_WEEK", ["Який тиждень?", "Обери:"], "t")
        page = (await client.get("/")).text
        assert "Який тиждень?" in page
        assert "Обери:" in page

    async def test_it_says_when_the_bot_is_still_reading_the_spreadsheet(self, client):
        for key in list(BASE):
            await configstore.delete_key(key, "test")
        assert "читає налаштування з таблиці" in (await client.get("/")).text


class TestTheEditForm:
    async def test_it_prefills_what_is_stored(self, client):
        assert "Скільки розмов?" in (await client.get("/edit/QUESTION_1")).text

    async def test_it_offers_an_extra_empty_box_for_another_phrasing(self, client):
        """Adding a phrasing should not need a button or any JavaScript."""
        page = (await client.get("/edit/QUESTION_1")).text
        assert page.count('name="value"') == 2

    async def test_a_single_value_key_gets_exactly_one_box(self, client):
        page = (await client.get("/edit/START_DATE")).text
        assert page.count('name="value"') == 1

    async def test_an_unknown_key_is_a_404(self, client):
        assert (await client.get("/edit/NO_SUCH_KEY")).status_code == 404

    async def test_signed_out_visitors_cannot_open_it(self, store):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=webapp.app),
            base_url="https://testserver",
        ) as anon:
            assert (await anon.get("/edit/QUESTION_1")).status_code == 307


class TestReviewBeforeSave:
    async def test_a_valid_change_shows_was_and_will_be(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        response = await client.post(
            "/edit/QUESTION_1",
            data={"value": ["Скільки було розмов?", ""], "csrf": token},
        )
        assert response.status_code == 200
        assert "Скільки розмов?" in response.text        # was
        assert "Скільки було розмов?" in response.text   # will be

    async def test_nothing_is_written_by_the_review_step(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        await client.post(
            "/edit/QUESTION_1", data={"value": ["Змінено", ""], "csrf": token}
        )
        assert (await configstore.load())["QUESTION_1"] == ["Скільки розмов?"]

    async def test_an_unchanged_value_skips_the_review(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        response = await client.post(
            "/edit/QUESTION_1",
            data={"value": ["Скільки розмов?", ""], "csrf": token},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/#QUESTION_1"

    async def test_a_refused_change_keeps_what_was_typed(self, client):
        token = await csrf_from(client, "/edit/START_DATE")
        response = await client.post(
            "/edit/START_DATE", data={"value": ["7 вересня"], "csrf": token}
        )
        assert response.status_code == 200
        assert "dd.mm.yyyy" in response.text
        assert "7 вересня" in response.text

    async def test_the_way_back_carries_the_draft(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        response = await client.post(
            "/edit/QUESTION_1",
            data={"value": ["Чернетка", ""], "csrf": token, "back": "1"},
        )
        assert "Чернетка" in response.text

    async def test_a_missing_csrf_token_is_refused(self, client):
        response = await client.post(
            "/edit/QUESTION_1", data={"value": ["Змінено"]}
        )
        assert response.status_code == 400


class TestSaving:
    async def test_a_save_lands_in_the_database(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        response = await client.post(
            "/save/QUESTION_1", data={"value": ["Скільки було розмов?"], "csrf": token}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/?saved=QUESTION_1#QUESTION_1"
        assert (await configstore.load())["QUESTION_1"] == ["Скільки було розмов?"]

    async def test_phrasings_keep_their_order(self, client):
        token = await csrf_from(client, "/edit/SELECT_WEEK")
        await client.post(
            "/save/SELECT_WEEK",
            data={"value": ["перший", "другий", "третій"], "csrf": token},
        )
        assert (await configstore.load())["SELECT_WEEK"] == [
            "перший", "другий", "третій"
        ]

    async def test_a_blank_box_is_dropped_not_stored(self, client):
        """The empty box at the end of the form must not become a phrasing the
        bot could pick and send as an empty message."""
        token = await csrf_from(client, "/edit/SELECT_WEEK")
        await client.post(
            "/save/SELECT_WEEK", data={"value": ["Обери:", "   ", ""], "csrf": token}
        )
        assert (await configstore.load())["SELECT_WEEK"] == ["Обери:"]

    async def test_clearing_every_box_is_refused(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        response = await client.post(
            "/save/QUESTION_1", data={"value": ["", "  "], "csrf": token}
        )
        assert response.status_code == 200
        assert (await configstore.load())["QUESTION_1"] == ["Скільки розмов?"]

    async def test_a_stale_tab_cannot_write_something_invalid(self, client):
        """The review step is a round trip, so the save path validates again
        rather than trusting that it already passed."""
        token = await csrf_from(client, "/edit/START_DATE")
        response = await client.post(
            "/save/START_DATE", data={"value": ["не дата"], "csrf": token}
        )
        assert response.status_code == 200
        assert (await configstore.load())["START_DATE"] == ["07.09.2026"]

    async def test_a_missing_csrf_token_writes_nothing(self, client):
        response = await client.post("/save/QUESTION_1", data={"value": ["Змінено"]})
        assert response.status_code == 400
        assert (await configstore.load())["QUESTION_1"] == ["Скільки розмов?"]

    async def test_a_save_bumps_the_version_so_the_bot_reloads(self, client):
        before = await configstore.version()
        token = await csrf_from(client, "/edit/QUESTION_1")
        await client.post(
            "/save/QUESTION_1", data={"value": ["Інакше"], "csrf": token}
        )
        assert await configstore.version() > before

    async def test_the_bot_can_read_back_what_was_saved(self, client):
        """The whole point of the tool. A save the bot would refuse to load is
        a save that takes the bot down on its next restart."""
        from bot import lexicon

        token = await csrf_from(client, "/edit/SELECT_WEEK")
        await client.post(
            "/save/SELECT_WEEK", data={"value": ["Обери тиждень", "Який тиждень"],
                                       "csrf": token},
        )
        assert await lexicon.load_from_store() is True
        assert lexicon.variants("SELECT_WEEK") == ["Обери тиждень", "Який тиждень"]


class TestThePatternPreview:
    async def test_it_shows_which_columns_a_week_lands_in(self, client):
        """The one thing the digits cannot tell you."""
        page = (await client.get("/edit/QUESTION_PATTERN")).text
        assert "Стовпці" in page
        assert "Тиждень" in page

    async def test_the_preview_follows_the_draft_not_the_stored_value(self, client):
        token = await csrf_from(client, "/edit/QUESTION_PATTERN")
        response = await client.post(
            "/edit/QUESTION_PATTERN", data={"value": ["4"], "csrf": token}
        )
        # Four questions a week starting at column C: C-F, then G-J.
        assert "C–F" in response.text
        assert "G–J" in response.text

    def test_the_columns_match_the_bot_s_own_arithmetic(self):
        """week_to_column_coords is what actually writes to the sheet; a
        preview that disagreed with it would be worse than none."""
        from bot import lexicon
        from bot.utils import maths

        rows = webapp.pattern_preview("232", "07.09.2026", "30.12.2026")
        lexicon._variants.update(
            {
                "START_DATE": ["07.09.2026"],
                "END_DATE": ["30.12.2026"],
                "QUESTION_PATTERN": ["232"],
            }
        )
        try:
            for row in rows[:6]:
                first, last = maths.week_to_column_coords(row["week"]).split()
                expected = f"{first}–{last}" if row["questions"] > 1 else first
                assert row["columns"] == expected, row
        finally:
            for key in ("START_DATE", "END_DATE", "QUESTION_PATTERN"):
                lexicon._variants.pop(key, None)

    def test_an_unparseable_pattern_has_no_preview(self):
        assert webapp.pattern_preview("abc", "07.09.2026", "30.12.2026") is None

    def test_a_semester_spanning_new_year_has_no_preview(self):
        assert webapp.pattern_preview("2", "07.09.2026", "30.01.2027") is None


class TestHistory:
    async def test_a_save_appears_with_who_made_it(self, client):
        token = await csrf_from(client, "/edit/QUESTION_1")
        await client.post(
            "/save/QUESTION_1", data={"value": ["Нове питання"], "csrf": token}
        )
        page = (await client.get("/history")).text
        assert "@yaros" in page
        assert "Нове питання" in page
        assert "Скільки розмов?" in page, "the old value should be shown too"

    async def test_it_is_protected(self, store):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=webapp.app),
            base_url="https://testserver",
        ) as anon:
            assert (await anon.get("/history")).status_code == 307


class TestExport:
    async def test_it_returns_the_configuration_as_a_file(self, client):
        response = await client.get("/export.json")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert response.json()["QUESTION_1"] == ["Скільки розмов?"]

    async def test_it_is_protected(self, store):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=webapp.app),
            base_url="https://testserver",
        ) as anon:
            assert (await anon.get("/export.json")).status_code == 307


class TestKeysNothingReads:
    """migrate_config.py imports Group.UNUSED keys, because after the cutover
    the database is the only copy of the old wording. So they have to be
    visible: a stored value nobody can see is a stored value nobody can clean
    up -- the same trap as a key the registry does not describe."""

    async def test_an_unused_key_with_a_value_is_shown(self, client):
        unused = next(f for f in registry.FIELDS if f.group is registry.Group.UNUSED)
        await configstore.set_variants(unused.key, ["Старе формулювання"], "test")

        page = (await client.get("/")).text
        assert "Старе формулювання" in page
        assert registry.GROUP_LABELS_UK[registry.Group.UNUSED] in page

    async def test_it_is_read_only(self, client):
        unused = next(f for f in registry.FIELDS if f.group is registry.Group.UNUSED)
        await configstore.set_variants(unused.key, ["Старе формулювання"], "test")

        page = (await client.get("/")).text
        assert f'href="/edit/{unused.key}"' not in page
        assert "Бот нічого з цього не читає" in page

    async def test_an_unused_key_with_no_value_is_not_listed_at_all(self, client):
        """Dozens of empty rows reading "not set" for keys nothing reads would
        bury the section that matters."""
        page = (await client.get("/")).text
        assert registry.GROUP_LABELS_UK[registry.Group.UNUSED] not in page

    async def test_it_is_still_in_the_backup(self, client):
        unused = next(f for f in registry.FIELDS if f.group is registry.Group.UNUSED)
        await configstore.set_variants(unused.key, ["Старе формулювання"], "test")
        assert (await client.get("/export.json")).json()[unused.key] == [
            "Старе формулювання"
        ]
