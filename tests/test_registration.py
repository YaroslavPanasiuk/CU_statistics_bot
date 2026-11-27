"""Registration no longer discloses the roster.

/start used to reply with an inline keyboard listing every unregistered
volunteer's full name, to anyone who found the bot, and accept any reg_<id>
callback. A stranger could read the roster and claim someone's identity.
"""
from types import SimpleNamespace

import pytest

from bot.db import database
from bot.handlers import common


def volunteer(volunteer_id, full_name):
    return SimpleNamespace(id=volunteer_id, full_name=full_name, tg_id=None)


ROSTER = [
    volunteer(1, "Польовий Саша"),
    volunteer(2, "Руцький Микита"),
    volunteer(3, "Шевченко Тарас"),
    volunteer(4, "Одноіменний"),
]


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    async def get_unregistered_users():
        return list(ROSTER)

    monkeypatch.setattr(database, "get_unregistered_users", get_unregistered_users)


class TestNameMatching:
    async def test_matches_an_exact_name(self):
        found = await common.find_unregistered_volunteer("Польовий Саша")
        assert found.id == 1

    async def test_ignores_case(self):
        found = await common.find_unregistered_volunteer("польовий саша")
        assert found.id == 1

    async def test_ignores_surrounding_and_repeated_whitespace(self):
        found = await common.find_unregistered_volunteer("  Польовий   Саша  ")
        assert found.id == 1

    async def test_accepts_the_name_in_either_order(self):
        """The sheet stores "Surname Given"; people type it both ways."""
        found = await common.find_unregistered_volunteer("Саша Польовий")
        assert found.id == 1

    async def test_matches_a_single_token_name(self):
        found = await common.find_unregistered_volunteer("Одноіменний")
        assert found.id == 4

    async def test_unknown_name_returns_none(self):
        assert await common.find_unregistered_volunteer("Ніхто Невідомий") is None

    async def test_empty_input_returns_none(self):
        assert await common.find_unregistered_volunteer("   ") is None

    async def test_a_partial_name_does_not_match(self):
        """Otherwise "Саша" alone would claim the first Саша on the roster."""
        assert await common.find_unregistered_volunteer("Саша") is None

    async def test_an_exact_match_wins_over_a_reordered_one(self):
        """Exact is unambiguous even when another volunteer has the same
        tokens in a different order."""
        found = await common.find_unregistered_volunteer("Шевченко Тарас")
        assert found.id == 3

    async def test_an_ambiguous_reordering_returns_none(self, monkeypatch):
        """When only the order-insensitive pass matches, and it matches more
        than one volunteer, refuse rather than guess."""
        async def duplicates():
            return [
                volunteer(5, "Коваль Петро Іванович"),
                volunteer(6, "Петро Коваль Іванович"),
            ]

        monkeypatch.setattr(database, "get_unregistered_users", duplicates)
        # Matches neither exactly; same token multiset as both.
        assert await common.find_unregistered_volunteer("Іванович Коваль Петро") is None


class TestNormaliseName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Польовий Саша", "польовий саша"),
            ("  ПОЛЬОВИЙ   САША ", "польовий саша"),
            ("", ""),
        ],
    )
    def test_collapses_case_and_whitespace(self, raw, expected):
        assert common.normalise_name(raw) == expected


class TestRosterIsNotDisclosed:
    def test_start_no_longer_builds_a_roster_keyboard(self):
        import inspect

        source = inspect.getsource(common.cmd_start)
        assert "get_unregistered_keyboard" not in source

    def test_the_roster_keyboard_no_longer_exists(self):
        from bot.utils import keyboards

        assert not hasattr(keyboards, "get_unregistered_keyboard")

    def test_no_reg_callback_handler_remains(self):
        """Any reg_<id> callback was accepted from anyone."""
        names = [h.callback.__name__ for h in common.router.callback_query.handlers]
        assert "complete_registration" not in names

    def test_start_asks_for_a_name_and_waits_for_it(self):
        import inspect

        source = inspect.getsource(common.cmd_start)
        assert "Registration.waiting_for_name" in source

    def test_the_name_prompt_is_state_gated(self):
        """A stray message from a stranger must not be treated as a claim."""
        handler = next(
            h for h in common.router.message.handlers
            if h.callback.__name__ == "process_registration_name"
        )
        assert any("waiting_for_name" in str(f.callback) for f in handler.filters)


class TestStartWorksForBothAudiences:
    def test_unregistered_users_get_the_name_prompt(self):
        names = [h.callback.__name__ for h in common.router.message.handlers]
        assert names[0] == "cmd_start"

    def test_registered_users_still_get_a_greeting(self):
        """Both audiences need a /start. Without this a registered user's
        /start falls all the way through to "unknown command"."""
        names = [h.callback.__name__ for h in common.registered_router.message.handlers]
        assert "cmd_start_registered" in names

    def test_the_greeting_handles_a_single_word_name(self):
        """`full_name.split()[1]` raised IndexError for anyone whose roster
        entry is one word."""
        from bot.utils.formatters import display_name

        assert display_name("Одноіменний") == "Одноіменний"
        assert display_name("Польовий Саша") == "Саша"
        assert display_name("") == ""


class TestUnregisterMe:
    def test_volunteers_can_remove_their_own_registration(self):
        names = [h.callback.__name__ for h in common.registered_router.message.handlers]
        assert "cmd_unregister_me" in names
