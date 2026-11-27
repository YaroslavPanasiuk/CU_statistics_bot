"""Tests for the Google Sheets access layer.

No network: gspread is replaced with fakes so the caching and retry
behaviour can be asserted directly.
"""
from types import SimpleNamespace

import pytest
from gspread.exceptions import APIError

from bot.utils import spreadsheets


class FakeAPIError(APIError):
    def __init__(self, status):
        self.response = SimpleNamespace(status_code=status)
        Exception.__init__(self, f"fake API error {status}")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(spreadsheets.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def clean_cache():
    spreadsheets.reset_sheet_cache()
    yield
    spreadsheets.reset_sheet_cache()


class TestWithRetry:
    def test_returns_the_value_on_first_success(self):
        assert spreadsheets.with_retry(lambda: "ok") == "ok"

    def test_retries_through_a_rate_limit_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise FakeAPIError(429)
            return "ok"

        assert spreadsheets.with_retry(flaky) == "ok"
        assert len(calls) == 3

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retries_every_transient_status(self, status):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise FakeAPIError(status)
            return "ok"

        assert spreadsheets.with_retry(flaky) == "ok"

    def test_gives_up_after_the_attempt_limit(self):
        calls = []

        def always_429():
            calls.append(1)
            raise FakeAPIError(429)

        with pytest.raises(APIError):
            spreadsheets.with_retry(always_429, attempts=3)
        assert len(calls) == 3

    def test_does_not_retry_a_client_error(self):
        """A 404 or a permissions failure will not fix itself."""
        calls = []

        def not_found():
            calls.append(1)
            raise FakeAPIError(404)

        with pytest.raises(APIError):
            spreadsheets.with_retry(not_found)
        assert len(calls) == 1

    def test_passes_through_arguments(self):
        assert spreadsheets.with_retry(lambda a, b=0: a + b, 1, b=2) == 3


class TestWorksheetCaching:
    @pytest.fixture
    def fake_gspread(self, monkeypatch):
        """Count how often a client is built and a spreadsheet opened."""
        counts = {"clients": 0, "opens": 0}

        class FakeSpreadsheet:
            def worksheet(self, name):
                return SimpleNamespace(title=name)

        class FakeClient:
            def open_by_url(self, url):
                counts["opens"] += 1
                return FakeSpreadsheet()

        def service_account_from_dict(_creds):
            counts["clients"] += 1
            return FakeClient()

        monkeypatch.setattr(
            spreadsheets.gspread, "service_account_from_dict", service_account_from_dict
        )
        monkeypatch.setattr(spreadsheets.Lexicon, "WORKSHEET_NAME", "Осінь", raising=False)
        return counts

    def test_repeated_access_opens_the_spreadsheet_once(self, fake_gspread):
        """Each operation used to build a client and re-open the spreadsheet:
        three extra round trips per call against a 60/min quota."""
        for _ in range(10):
            spreadsheets.data_worksheet()
        assert fake_gspread == {"clients": 1, "opens": 1}

    def test_a_different_worksheet_name_is_fetched_once_each(self, fake_gspread, monkeypatch):
        spreadsheets.data_worksheet()
        monkeypatch.setattr(spreadsheets.Lexicon, "WORKSHEET_NAME", "Весна", raising=False)
        spreadsheets.data_worksheet()
        spreadsheets.data_worksheet()
        assert fake_gspread == {"clients": 1, "opens": 2}

    def test_reset_forces_a_fresh_client(self, fake_gspread):
        spreadsheets.data_worksheet()
        spreadsheets.reset_sheet_cache()
        spreadsheets.data_worksheet()
        assert fake_gspread == {"clients": 2, "opens": 2}

    def test_returns_the_worksheet_named_by_the_config_sheet(self, fake_gspread):
        assert spreadsheets.data_worksheet().title == "Осінь"


class TestVolunteerRowLookup:
    async def test_uses_the_cached_roster_instead_of_refetching(self, monkeypatch):
        """volunteer_to_row_coord ran load_volunteer_list() -- a full sheet
        download -- on every single export."""
        fetches = []

        def read_all_values():
            fetches.append(1)
            return [["", ""], ["№", "Учні"], ["", "Перший"], ["", "Другий"]]

        monkeypatch.setattr(spreadsheets, "read_all_values", read_all_values)

        await spreadsheets.load_volunteer_list()
        for _ in range(5):
            row = await spreadsheets.volunteer_to_row_coord(SimpleNamespace(full_name="Другий"))

        assert row == 4
        assert len(fetches) == 1

    async def test_first_row_is_sheet_row_3(self, monkeypatch):
        monkeypatch.setattr(
            spreadsheets, "read_all_values",
            lambda: [["", ""], ["№", "Учні"], ["", "Перший"]],
        )
        await spreadsheets.load_volunteer_list()
        assert await spreadsheets.volunteer_to_row_coord(SimpleNamespace(full_name="Перший")) == 3

    async def test_unknown_volunteer_reports_minus_one(self, monkeypatch):
        monkeypatch.setattr(
            spreadsheets, "read_all_values",
            lambda: [["", ""], ["№", "Учні"], ["", "Перший"]],
        )
        await spreadsheets.load_volunteer_list()
        assert await spreadsheets.volunteer_to_row_coord(SimpleNamespace(full_name="Ніхто")) == -1

    async def test_fetches_the_roster_when_nothing_is_cached(self, monkeypatch):
        fetches = []

        def read_all_values():
            fetches.append(1)
            return [["", ""], ["№", "Учні"], ["", "Перший"]]

        monkeypatch.setattr(spreadsheets, "read_all_values", read_all_values)
        assert await spreadsheets.volunteer_to_row_coord(SimpleNamespace(full_name="Перший")) == 3
        assert len(fetches) == 1


class TestAnswersFromRow:
    def test_maps_cells_to_numbered_answers(self):
        assert spreadsheets.answers_from_row(["4", "2", "0"]) == {
            "answer_1": "4", "answer_2": "2", "answer_3": "0",
        }

    def test_drops_blank_cells(self):
        """The sheet is authoritative, but a blank cell means "nothing
        recorded", not "erase the answer the volunteer submitted"."""
        assert spreadsheets.answers_from_row(["4", "", "0"]) == {"answer_1": "4", "answer_3": "0"}

    def test_drops_whitespace_only_cells(self):
        assert spreadsheets.answers_from_row(["  ", "1"]) == {"answer_2": "1"}

    def test_an_entirely_blank_week_yields_nothing(self):
        """The importer skips the week rather than writing empty strings over
        stored answers on every startup and nightly run."""
        assert spreadsheets.answers_from_row(["", "", ""]) == {}

    def test_zero_is_a_real_answer(self):
        assert spreadsheets.answers_from_row(["0"]) == {"answer_1": "0"}

    def test_numbering_is_positional_not_sequential(self):
        """answer_N must track the column, so a blank third cell does not
        renumber the fourth answer into its place."""
        assert spreadsheets.answers_from_row(["a", "b", "", "d"]) == {
            "answer_1": "a", "answer_2": "b", "answer_4": "d",
        }


class TestFetchUsersWithNoStats:
    @pytest.fixture(autouse=True)
    def semester_config(self, monkeypatch):
        monkeypatch.setattr(spreadsheets.Lexicon, "START_DATE", "01.09.2025", raising=False)
        monkeypatch.setattr(spreadsheets.Lexicon, "END_DATE", "21.12.2025", raising=False)
        monkeypatch.setattr(spreadsheets.Lexicon, "QUESTION_PATTERN", "2", raising=False)

    def sheet(self, monkeypatch, rows):
        monkeypatch.setattr(spreadsheets, "read_range", lambda _range: rows)

    async def test_lists_only_volunteers_with_an_empty_week(self, monkeypatch):
        self.sheet(monkeypatch, [
            ["", ""], ["Учні", ""],
            ["Заповнив Хтось", "4", "2"],
            ["Не Заповнив", "", ""],
        ])
        assert await spreadsheets.fetch_users_with_no_stats(36) == ["Не Заповнив"]

    async def test_a_partially_filled_week_counts_as_filled(self, monkeypatch):
        self.sheet(monkeypatch, [
            ["", ""], ["Учні", ""],
            ["Частково Заповнив", "4", ""],
        ])
        assert await spreadsheets.fetch_users_with_no_stats(36) == []

    async def test_blank_name_rows_are_ignored(self, monkeypatch):
        self.sheet(monkeypatch, [["", ""], ["Учні", ""], ["", "", ""], ["Хтось Інший", "", ""]])
        assert await spreadsheets.fetch_users_with_no_stats(36) == ["Хтось Інший"]

    async def test_a_week_with_no_questions_reports_nobody(self, monkeypatch):
        """`record[-0:]` is the whole row, so this used to report that nobody
        was behind regardless of what the sheet held."""
        self.sheet(monkeypatch, [["", ""], ["Учні", ""], ["Не Заповнив", "", ""]])
        assert await spreadsheets.fetch_users_with_no_stats(99) == []

    async def test_raises_instead_of_returning_none(self, monkeypatch):
        """Returning None made the reminder job die with "NoneType is not
        iterable" and stop sending reminders without explanation."""
        def boom(_range):
            raise RuntimeError("sheets is down")

        monkeypatch.setattr(spreadsheets, "read_range", boom)
        with pytest.raises(RuntimeError):
            await spreadsheets.fetch_users_with_no_stats(36)
