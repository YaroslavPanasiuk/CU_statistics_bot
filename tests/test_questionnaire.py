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


class TestSemesterMessagesSubstitutePlaceholders:
    """SEMESTER_NOT_STARTED is a template in the config sheet.

    It was sent through select_random_line(), so volunteers received the
    literal "{0}" in the message.
    """

    @pytest.fixture(autouse=True)
    def templates(self, monkeypatch):
        # The module fixture puts the semester in the past; these cases need it
        # ahead of us so the "not started yet" branch is the one taken.
        monkeypatch.setattr(Lexicon, "START_DATE", "01.09.2099", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", "21.12.2099", raising=False)
        monkeypatch.setattr(Lexicon, "SEMESTER_NOT_STARTED", "Йоу, {0}, семестр ще не почався.", raising=False)
        monkeypatch.setattr(Lexicon, "SEMESTER_OVER", "Семестр завершено.", raising=False)
        monkeypatch.setattr(Lexicon, "BOT_UNAVAILABLE", "Зараз недоступно", raising=False)

    async def test_a_known_volunteer_is_addressed_by_name(self):
        from types import SimpleNamespace

        message = FakeMessage()
        volunteer = SimpleNamespace(full_name="Польовий Саша", id=1, tg_id=5)
        await common.bot_unavailable(message, volunteer)

        assert "Саша" in message.answers[0]
        assert "{0}" not in message.answers[0]

    async def test_no_literal_placeholder_for_an_unknown_user(self):
        """Anyone can reach this router, registered or not."""
        message = FakeMessage()
        await common.bot_unavailable(message, None)
        assert "{0}" not in message.answers[0]

    async def test_a_template_free_message_is_unharmed(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "START_DATE", "01.01.2020", raising=False)
        monkeypatch.setattr(Lexicon, "END_DATE", "31.12.2020", raising=False)
        message = FakeMessage()
        await common.bot_unavailable(message, None)
        assert message.answers == ["Семестр завершено."]


class TestRegistrationPrompt:
    def test_asks_for_a_typed_name_rather_than_a_list(self):
        """SELECT_YOUR_NAME reads "choose yourself from the list", which has
        not been true since the roster keyboard was removed."""
        import inspect

        source = inspect.getsource(common.cmd_start)
        assert "ASK_NAME" in source
        # Not merely absent from the file -- absent as a *key being read*.
        # It still appears in a comment explaining why it was dropped.
        assert "select_random_line('SELECT_YOUR_NAME')" not in source
        assert "text('SELECT_YOUR_NAME'" not in source
