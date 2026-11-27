"""Handler registration order is load-bearing.

aiogram matches message handlers in registration order, and several of them
are catch-alls. In particular `process_any_answer` matches *any* message while
the user is in StatisticsCollection.waiting_for_answer, so anything that must
stay usable mid-questionnaire has to be registered above it.
"""
from types import SimpleNamespace

import pytest

from bot.handlers import admin, common


def order(router):
    return [h.callback.__name__ for h in router.message.handlers]


def router_filters(router):
    """Types of the filters applied to every message entering a router."""
    return [type(f.callback) for f in router.message._handler.filters]


class TestRegisteredRouterOrder:
    @pytest.fixture
    def names(self):
        return order(common.registered_router)

    def test_cancel_precedes_the_answer_catch_all(self, names):
        """Without this, /cancel is swallowed as an answer and the user is
        stuck in the questionnaire with no way out."""
        assert names.index("cmd_cancel") < names.index("process_any_answer")

    def test_get_my_stats_precedes_the_answer_catch_all(self, names):
        """Regression: get_my_stats was registered after the state handler, so
        pressing the stats button mid-questionnaire recorded the button label
        as an answer instead."""
        assert names.index("get_my_stats") < names.index("process_any_answer")

    def test_fill_buttons_precede_the_answer_catch_all(self, names):
        for handler in ("cmd_fill_stats", "start_old_stats"):
            assert names.index(handler) < names.index("process_any_answer"), handler

    def test_every_menu_handler_precedes_the_answer_catch_all(self, names):
        """The reply keyboard's three buttons plus /cancel must all survive."""
        menu = {"cmd_fill_stats", "start_old_stats", "get_my_stats", "cmd_cancel"}
        answer_index = names.index("process_any_answer")
        assert menu <= set(names[:answer_index])

    def test_answer_handler_ignores_commands(self):
        """Regression, with physical evidence in production.

        `process_any_answer` matched *any* message in waiting_for_answer, so a
        command typed mid-questionnaire was stored as the answer. Row 16820 of
        the live statistics table is {"answer_1": "/remind_admin"}. Admin
        commands have to fall through to admin.router instead.
        """
        from aiogram import F

        answer_filter = F.text & ~F.text.startswith("/")
        for command in ("/remind_admin", "/cancel", "/broadcast hello"):
            assert not answer_filter.resolve(SimpleNamespace(text=command)), command

    def test_answer_handler_accepts_ordinary_text(self):
        from aiogram import F

        answer_filter = F.text & ~F.text.startswith("/")
        for answer in ("4", "0", "не знаю"):
            assert answer_filter.resolve(SimpleNamespace(text=answer)), answer

    def test_answer_handler_ignores_non_text_messages(self):
        """A sticker used to be stored as a None answer, which later summed as 0."""
        from aiogram import F

        answer_filter = F.text & ~F.text.startswith("/")
        assert not answer_filter.resolve(SimpleNamespace(text=None))

    def test_answer_catch_all_precedes_the_bible_verse_fallback(self, names):
        assert names.index("process_any_answer") < names.index("default_handler")

    def test_default_handler_is_last(self, names):
        assert names[-1] == "default_handler"


class TestRouterInclusionOrder:
    def test_registered_router_is_included_before_the_fallback_router(self, dispatcher):
        """common.router ends in a "you are not registered" catch-all, so it
        must come last or registered users would hit it."""
        included = list(dispatcher.sub_routers)
        assert included.index(common.registered_router) < included.index(common.router)
        assert included.index(admin.router) < included.index(common.router)

    def test_registered_fallback_is_included_after_admin_router(self, dispatcher):
        """The unknown-command reply must not shadow admin commands: admins
        are registered volunteers, so a catch-all on registered_router would
        swallow every one of them."""
        included = list(dispatcher.sub_routers)
        assert included.index(admin.router) < included.index(common.fallback_router)
        assert included[-1] is common.fallback_router

    def test_each_router_declares_its_audience(self):
        """`router` ends in a "you are not registered" catch-all but carried no
        filter at all, so registered users sending an unknown command were
        told they were not registered."""
        from bot.filters.is_registered import IsNotRegistered, IsRegistered

        assert router_filters(common.router) == [IsNotRegistered]
        assert router_filters(common.registered_router) == [IsRegistered]
        assert router_filters(common.fallback_router) == [IsRegistered]

    def test_unavailable_router_is_still_not_registered(self, dispatcher):
        """Known gap (Phase 4): BotAvailable has no effect because this router
        is never included, so the semester-range check is dead code."""
        assert common.unavailable_router not in dispatcher.sub_routers
