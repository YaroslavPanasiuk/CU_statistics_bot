"""Tests for the destructive guard on volunteer syncing.

`sync_volunteers` deletes volunteers absent from the roster, so these cases
are asserted without a live database: the guard must fire before any session
is opened.
"""
import pytest

from bot.db.database import sync_volunteers


class TestSyncVolunteersGuard:
    async def test_refuses_an_empty_roster(self):
        """`not_in([])` matches every row, so an empty roster would delete the
        whole volunteer table. A Sheets glitch must not be able to do that."""
        with pytest.raises(ValueError, match="empty volunteer roster"):
            await sync_volunteers([])

    async def test_refuses_none(self):
        """load_volunteer_list() used to return None on any Sheets error,
        which crashed startup with `TypeError: 'NoneType' is not iterable`."""
        with pytest.raises((ValueError, TypeError)):
            await sync_volunteers(None)
