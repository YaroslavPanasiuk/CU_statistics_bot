"""Tests for the pure row-parsing helpers in bot.utils.spreadsheets."""
from bot.utils.spreadsheets import volunteer_names


class TestVolunteerNames:
    def test_skips_the_two_header_rows(self):
        values = [
            ["", "", "Вересень 7-13"],
            ["№", "Учні Христа", "ЄВАНГЕЛІЄ"],
            ["", "Польовий Саша", ""],
            ["", "Руцький Микита", "0"],
        ]
        assert volunteer_names(values) == ["Польовий Саша", "Руцький Микита"]

    def test_ignores_trailing_blank_padding_rows(self):
        """The real sheet ends in blank rows, not a totals row.

        The previous `[2:-1]` slice returned those blanks as volunteers named
        "", which produced an empty-named database row that appeared as a
        blank registration button.
        """
        values = [["", "", ""], ["№", "Учні Христа", ""]]
        values += [["", "Волонтер 1", "4"], ["", "Волонтер 2", "2"]]
        values += [["", "", ""]] * 13
        assert volunteer_names(values) == ["Волонтер 1", "Волонтер 2"]

    def test_keeps_the_final_volunteer(self):
        """`[2:-1]` silently dropped whoever was last when no totals row existed."""
        values = [["", ""], ["№", "Учні Христа"], ["", "Перший"], ["", "Останній"]]
        assert volunteer_names(values) == ["Перший", "Останній"]

    def test_strips_surrounding_whitespace(self):
        values = [["", ""], ["№", "Учні Христа"], ["", "  Волонтер  "]]
        assert volunteer_names(values) == ["Волонтер"]

    def test_tolerates_short_rows(self):
        values = [["", ""], ["№", "Учні Христа"], [""], ["", "Волонтер"], []]
        assert volunteer_names(values) == ["Волонтер"]

    def test_empty_sheet_yields_empty_list(self):
        assert volunteer_names([["", ""], ["№", "Учні Христа"]]) == []
