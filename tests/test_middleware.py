"""The volunteer lookup happens once per update, in middleware.

Previously IsRegistered/IsNotRegistered on three routers plus several
handlers each ran their own SELECT for the same message.
"""
from types import SimpleNamespace

import pytest
from aiogram.dispatcher.event.handler import FilterObject

from bot.db import database
from bot.filters.is_registered import IsNotRegistered, IsRegistered
from bot.middlewares import VolunteerMiddleware


VOLUNTEER = SimpleNamespace(id=7, full_name="Волонтер Тест", tg_id=555)


@pytest.fixture
def lookups(monkeypatch):
    """Record every database lookup the dispatch path performs."""
    calls = []

    async def get_user_by_tg_id(tg_id):
        calls.append(tg_id)
        return VOLUNTEER if tg_id == VOLUNTEER.tg_id else None

    monkeypatch.setattr(database, "get_user_by_tg_id", get_user_by_tg_id)
    return calls


async def run_middleware(tg_id, lookups_data=None):
    data = {"event_from_user": SimpleNamespace(id=tg_id)} if tg_id is not None else {}
    captured = {}

    async def handler(event, data):
        captured.update(data)
        return "handled"

    result = await VolunteerMiddleware()(handler, SimpleNamespace(), data)
    return result, captured


class TestVolunteerMiddleware:
    async def test_injects_the_volunteer_row(self, lookups):
        _, data = await run_middleware(555)
        assert data["volunteer"] is VOLUNTEER

    async def test_injects_none_for_an_unknown_account(self, lookups):
        _, data = await run_middleware(999)
        assert data["volunteer"] is None

    async def test_looks_up_exactly_once_per_update(self, lookups):
        await run_middleware(555)
        assert lookups == [555]

    async def test_updates_with_no_user_skip_the_lookup(self, lookups):
        """Channel posts and similar arrive without event_from_user; the old
        filters would have raised on message.from_user.id."""
        _, data = await run_middleware(None)
        assert data["volunteer"] is None
        assert lookups == []

    async def test_passes_through_to_the_handler(self, lookups):
        result, _ = await run_middleware(555)
        assert result == "handled"


class TestFiltersReadTheInjectedVolunteer:
    def test_aiogram_will_supply_the_volunteer_kwarg(self):
        """The contract that makes this work: aiogram inspects a filter's
        signature and passes matching keys out of the middleware data."""
        for filt in (IsRegistered(), IsNotRegistered()):
            assert "volunteer" in FilterObject(filt).params, filt

    async def test_is_registered_matches_a_bound_volunteer(self):
        assert await IsRegistered()(SimpleNamespace(), volunteer=VOLUNTEER) is True
        assert await IsRegistered()(SimpleNamespace(), volunteer=None) is False

    async def test_is_not_registered_is_the_complement(self):
        assert await IsNotRegistered()(SimpleNamespace(), volunteer=None) is True
        assert await IsNotRegistered()(SimpleNamespace(), volunteer=VOLUNTEER) is False

    async def test_filters_perform_no_lookups_of_their_own(self, lookups):
        """They used to call get_user_by_tg_id directly, once per router."""
        _, data = await run_middleware(555)
        for filt in (IsRegistered(), IsNotRegistered()):
            await filt(SimpleNamespace(), volunteer=data["volunteer"])
        assert lookups == [555]


class TestMiddlewareIsRegisteredOuter:
    def test_registered_as_an_outer_middleware_on_updates(self, dispatcher):
        """It has to be outer, or router filters would run before the lookup."""
        outer = [type(m) for m in dispatcher.update.outer_middleware]
        assert VolunteerMiddleware in outer
