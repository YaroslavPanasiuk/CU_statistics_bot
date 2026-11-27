"""Starting and announcing the questionnaire."""
import pytest

from bot.handlers import common
from bot.lexicon import Lexicon


class FakeState:
    def __init__(self):
        self.data = {}
        self.state = None
        self.cleared = False

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.cleared = True
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


@pytest.fixture(autouse=True)
def semester(monkeypatch):
    monkeypatch.setattr(Lexicon, "START_DATE", "01.09.2025", raising=False)
    monkeypatch.setattr(Lexicon, "END_DATE", "21.12.2025", raising=False)
    monkeypatch.setattr(Lexicon, "QUESTION_PATTERN", "2", raising=False)
    monkeypatch.setattr(Lexicon, "QUESTION_1", "Скільки розмов?", raising=False)
    monkeypatch.setattr(Lexicon, "SELECTED_WEEK", "Тиждень", raising=False)
    monkeypatch.setattr(Lexicon, "BOT_UNAVAILABLE", "Зараз недоступно", raising=False)


class TestInitiateStatsQuestions:
    async def test_asks_the_first_question_for_a_valid_week(self):
        message, state = FakeMessage(), FakeState()
        await common.initiate_stats_questions(message, state, 36)

        assert "Скільки розмов?" in message.answers
        assert state.data["selected_week"] == 36
        assert state.state is not None

    async def test_announces_the_week_by_default(self):
        message, state = FakeMessage(), FakeState()
        await common.initiate_stats_questions(message, state, 36)
        assert any("Тиждень" in a for a in message.answers)

    async def test_announce_false_skips_the_duplicate_announcement(self):
        """process_week_callback already edits the picker message to show the
        chosen week; initiate used to announce it a second time."""
        message, state = FakeMessage(), FakeState()
        await common.initiate_stats_questions(message, state, 36, announce=False)

        assert not any("Тиждень" in a for a in message.answers)
        assert "Скільки розмов?" in message.answers

    async def test_refuses_a_week_that_asks_nothing(self):
        """It used to send QUESTION_1 regardless, so an out-of-semester week
        collected one answer and saved it. That is exactly how the stray
        week-33 row holding "/remind_admin" got into production.
        """
        message, state = FakeMessage(), FakeState()
        await common.initiate_stats_questions(message, state, 99)

        assert message.answers == ["Зараз недоступно"]
        assert state.state is None
        assert state.cleared is True
        assert "selected_week" not in state.data

    async def test_the_week_callback_does_not_announce_twice(self):
        import inspect

        source = inspect.getsource(common.process_week_callback)
        assert "announce=False" in source
