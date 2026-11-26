"""Tests for the week/column arithmetic.

This module is pure and carries the highest-risk logic in the project: the
QUESTION_PATTERN accumulation that maps an ISO week to a span of spreadsheet
columns. Two independent consumers must agree on that span:

  * `import_stats_from_sheet` slices a row with `row[i0:i1]` (0-based, end-exclusive)
  * `export_stats_to_sheet` writes the A1 range `col(i0+1)..col(i1)` (1-based, inclusive)

Those two conventions only line up because of the `+1`/`-1` adjustments inside
`week_to_indices`, so the agreement is asserted explicitly below.
"""
from datetime import datetime

import pytest

from bot.utils.maths import (
    check_week_in_range,
    number_to_excel_column,
    questions_in_week,
    week_to_column_coords,
    week_to_indices,
)


class TestNumberToExcelColumn:
    @pytest.mark.parametrize(
        ("num", "expected"),
        [(1, "A"), (2, "B"), (26, "Z"), (27, "AA"), (28, "AB"), (52, "AZ"), (53, "BA"), (702, "ZZ"), (703, "AAA")],
    )
    def test_converts_1_based_index_to_a1_letters(self, num, expected):
        assert number_to_excel_column(num) == expected

    def test_zero_yields_empty_string(self):
        # Known wart: there is no column 0, but the loop simply never runs and
        # returns "", producing an invalid A1 range rather than an error.
        assert number_to_excel_column(0) == ""


class TestQuestionsInWeek:
    def test_follows_the_pattern_from_the_start_week(self, semester):
        assert questions_in_week(36) == 3
        assert questions_in_week(37) == 2
        assert questions_in_week(38) == 3

    def test_pattern_repeats_cyclically(self, semester):
        assert questions_in_week(39) == 3
        assert questions_in_week(40) == 2
        assert questions_in_week(41) == 3

    def test_week_outside_the_semester_asks_nothing(self, semester):
        assert questions_in_week(35) == 0
        assert questions_in_week(52) == 0

    def test_defaults_to_the_current_week(self, monkeypatch):
        this_week = datetime.now().isocalendar()[1]
        from bot.lexicon import Lexicon

        year = datetime.now().year
        monkeypatch.setattr(Lexicon, "START_DATE", f"01.01.{year}", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", f"31.12.{year}", raising=False)
        monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "7", raising=False)
        assert questions_in_week() == 7
        assert questions_in_week(this_week) == 7


class TestWeekToIndices:
    def test_first_week_starts_at_column_c(self, semester):
        # Columns A and B hold row metadata (B is the volunteer name), so the
        # first answer column is C == 1-based 3 == 0-based 2.
        assert week_to_indices(36) == [2, 5]

    def test_spans_accumulate_without_gaps_or_overlap(self, semester):
        assert week_to_indices(36) == [2, 5]
        assert week_to_indices(37) == [5, 7]
        assert week_to_indices(38) == [7, 10]
        assert week_to_indices(39) == [10, 13]

    def test_span_width_always_equals_the_question_count(self, semester):
        for week in range(semester["start_week"], semester["end_week"] + 1):
            start, end = week_to_indices(week)
            assert end - start == questions_in_week(week), f"week {week}"

    def test_each_week_begins_where_the_previous_one_ended(self, semester):
        for week in range(semester["start_week"], semester["end_week"]):
            assert week_to_indices(week)[1] == week_to_indices(week + 1)[0], f"week {week}"

    def test_out_of_range_returns_none(self, semester):
        # Known wart (scheduled for Phase 6): callers such as
        # `week_to_column_coords` subscript this result immediately, so an
        # out-of-range week raises TypeError instead of a clear domain error.
        assert week_to_indices(35) is None
        with pytest.raises(TypeError):
            week_to_column_coords(35)


class TestWeekToColumnCoords:
    def test_returns_space_separated_a1_letters(self, semester):
        assert week_to_column_coords(36) == "C E"
        assert week_to_column_coords(37) == "F G"
        assert week_to_column_coords(38) == "H J"

    def test_a1_range_covers_exactly_the_slice_the_importer_reads(self, semester):
        """The export range and the import slice must describe the same cells."""
        for week in range(semester["start_week"], semester["end_week"] + 1):
            first_letter, last_letter = week_to_column_coords(week).split()
            slice_start, slice_end = week_to_indices(week)

            # A1 letters are 1-based and the range is inclusive on both ends.
            first_index = number_to_excel_column(slice_start + 1)
            last_index = number_to_excel_column(slice_end)
            assert (first_letter, last_letter) == (first_index, last_index)

            # Width of the inclusive A1 range == width of the exclusive slice.
            width = slice_end - slice_start
            assert width == questions_in_week(week), f"week {week}"


class TestCheckWeekInRange:
    def test_inclusive_on_both_ends(self, semester):
        assert check_week_in_range(36) is True
        assert check_week_in_range(51) is True

    def test_rejects_weeks_outside_the_semester(self, semester):
        assert check_week_in_range(35) is False
        assert check_week_in_range(52) is False

    def test_defaults_to_the_current_week(self, monkeypatch):
        from bot.lexicon import Lexicon

        year = datetime.now().year
        monkeypatch.setattr(Lexicon, "START_DATE", f"01.01.{year}", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", f"31.12.{year}", raising=False)
        assert check_week_in_range() is True

    def test_semester_spanning_new_year_is_never_in_range(self, monkeypatch):
        """Documents the single-calendar-year limitation.

        A semester from September to January gives start_week=36, end_week=2,
        so `start_week <= week <= end_week` is unsatisfiable and the bot would
        consider itself permanently unavailable. Phase 6 rejects such a config
        at startup rather than making this work.
        """
        from bot.lexicon import Lexicon

        monkeypatch.setattr(Lexicon, "START_DATE", "01.09.2025", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", "10.01.2026", raising=False)
        for week in (36, 50, 1, 2):
            assert check_week_in_range(week) is False
