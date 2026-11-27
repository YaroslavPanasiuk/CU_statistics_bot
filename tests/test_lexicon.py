"""Tests for Lexicon string handling."""
from types import SimpleNamespace

import pytest

from bot.lexicon import Lexicon, LexiconError, LexiconFilter, select_random_line


def message(text):
    return SimpleNamespace(text=text)


class TestLexiconFilter:
    async def test_matches_a_single_variant_key(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "FILL_STATISTICS", "Заповнити статистику", raising=False)
        assert await LexiconFilter("FILL_STATISTICS")(message("Заповнити статистику")) is True

    async def test_matches_every_variant_of_a_multi_variant_key(self, monkeypatch):
        """Regression: keyboard labels come from select_random_line().

        The filter used to compare against the whole raw value, so a key with
        ';;' variants produced a button whose label never matched its own
        handler -- the button silently fell through to the fallback handler.
        """
        raw = "Заповнити статистику;;Заповнити;;Статистика за тиждень"
        monkeypatch.setattr(Lexicon, "FILL_STATISTICS", raw, raising=False)
        f = LexiconFilter("FILL_STATISTICS")
        for variant in raw.split(";;"):
            assert await f(message(variant)) is True, variant

    async def test_every_label_select_random_line_can_emit_is_matched(self, monkeypatch):
        raw = "Один;;Два;;Три"
        monkeypatch.setattr(Lexicon, "MENU_KEY", raw, raising=False)
        f = LexiconFilter("MENU_KEY")
        for _ in range(50):
            assert await f(message(select_random_line("MENU_KEY"))) is True

    async def test_does_not_match_the_raw_value_with_separators(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "MENU_KEY", "Один;;Два", raising=False)
        assert await LexiconFilter("MENU_KEY")(message("Один;;Два")) is False

    async def test_rejects_unrelated_text(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "MENU_KEY", "Один;;Два", raising=False)
        assert await LexiconFilter("MENU_KEY")(message("щось інше")) is False

    async def test_missing_key_does_not_raise(self):
        assert await LexiconFilter("NO_SUCH_KEY")(message("anything")) is False

    async def test_non_text_message_does_not_raise(self, monkeypatch):
        """Stickers and photos arrive with text=None."""
        monkeypatch.setattr(Lexicon, "MENU_KEY", "Один", raising=False)
        assert await LexiconFilter("MENU_KEY")(message(None)) is False


class TestSelectRandomLine:
    def test_returns_the_only_variant(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "K", "single", raising=False)
        assert select_random_line("K") == "single"

    def test_always_returns_one_of_the_variants(self, monkeypatch):
        monkeypatch.setattr(Lexicon, "K", "a;;b;;c", raising=False)
        assert {select_random_line("K") for _ in range(100)} <= {"a", "b", "c"}

    def test_missing_key_names_the_key(self):
        """Callers used to get a bare AttributeError from deep inside a
        handler, with no hint that the config sheet was the problem."""
        with pytest.raises(LexiconError, match="DEFINITELY_NOT_A_KEY"):
            select_random_line("DEFINITELY_NOT_A_KEY")
