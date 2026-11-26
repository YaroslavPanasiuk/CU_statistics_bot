"""Tests for statistics formatting."""
import pytest

from bot.db.database import WeekStats
from bot.lexicon import Lexicon
from bot.utils.formatters import answer_sort_key, format_all_stats_for_user


@pytest.fixture
def question_names(monkeypatch):
    for n in range(1, 6):
        monkeypatch.setattr(Lexicon, f"QUESTION_{n}_NAME", f"Питання {n}", raising=False)


def row(stats, week=37):
    """One row as get_all_statistics_for_user() returns it."""
    return WeekStats(full_name="Волонтер Тест", stats=stats, week=week)


class TestAnswerSortKey:
    def test_orders_numerically_not_lexicographically(self):
        keys = ["answer_10", "answer_2", "answer_1"]
        assert sorted(keys, key=answer_sort_key) == ["answer_1", "answer_2", "answer_10"]

    def test_unrecognised_keys_sort_last_and_deterministically(self):
        keys = ["zzz", "answer_2", "selected_week", "answer_1"]
        assert sorted(keys, key=answer_sort_key) == ["answer_1", "answer_2", "selected_week", "zzz"]


class TestFormatAllStatsForUser:
    def test_no_stats_returns_a_notice(self):
        assert format_all_stats_for_user([]) == "Статистика недоступна."

    def test_output_order_is_stable_across_calls(self, question_names):
        """Regression: totals were iterated straight off a set, so the
        categories came back in a different order on every request."""
        stats_list = [row({"answer_3": "1", "answer_1": "2", "answer_2": "3"})]
        renders = {format_all_stats_for_user(stats_list) for _ in range(50)}
        assert len(renders) == 1

    def test_categories_appear_in_question_order(self, question_names):
        stats_list = [row({"answer_3": "0", "answer_1": "0", "answer_2": "0"})]
        text = format_all_stats_for_user(stats_list)
        positions = [text.index(f"Питання {n}") for n in (1, 2, 3)]
        assert positions == sorted(positions)

    def test_sums_each_category_across_weeks(self, question_names):
        stats_list = [
            row({"answer_1": "2", "answer_2": "1"}, week=37),
            row({"answer_1": "3", "answer_2": "4"}, week=38),
        ]
        text = format_all_stats_for_user(stats_list)
        assert "5" in text and "Питання 1" in text

    def test_missing_category_in_one_week_counts_as_zero(self, question_names):
        stats_list = [row({"answer_1": "2"}, week=37), row({"answer_1": "1", "answer_2": "9"}, week=38)]
        text = format_all_stats_for_user(stats_list)
        assert "Питання 2" in text and "9" in text

    def test_non_numeric_answers_count_as_zero(self, question_names):
        """Known wart (Phase 6): answers are never validated at collection
        time, so free text is silently summed as 0 with no indication."""
        stats_list = [row({"answer_1": "не знаю"}), row({"answer_1": "4"})]
        text = format_all_stats_for_user(stats_list)
        assert "4" in text
