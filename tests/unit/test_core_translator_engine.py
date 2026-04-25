# -*- coding: utf-8 -*-
"""
Тесты для src/core/translator_engine.py — движок перевода через OpenClaw.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.translator_engine import (
    TranslationResult,
    build_translation_prompt,
    translate_text,
)

# ------------------------------------------------------------------
# build_translation_prompt
# ------------------------------------------------------------------


class TestBuildTranslationPrompt:
    def test_es_to_ru(self) -> None:
        prompt = build_translation_prompt("Hola mundo", "es", "ru")
        assert "испанского" in prompt
        assert "русский" in prompt
        assert "Hola mundo" in prompt

    def test_en_to_ru(self) -> None:
        prompt = build_translation_prompt("Hello world", "en", "ru")
        assert "английского" in prompt

    def test_unknown_lang(self) -> None:
        prompt = build_translation_prompt("test", "xx", "yy")
        assert "xx" in prompt
        assert "yy" in prompt

    def test_contains_only_instruction(self) -> None:
        prompt = build_translation_prompt("text", "es", "ru")
        assert "ТОЛЬКО перевод" in prompt


# ------------------------------------------------------------------
# TranslationResult dataclass
# ------------------------------------------------------------------


class TestTranslationResult:
    def test_fields(self) -> None:
        r = TranslationResult(
            original="Hola",
            translated="Привет",
            src_lang="es",
            tgt_lang="ru",
            latency_ms=1500,
            model_id="google/gemini-3-flash",
        )
        assert r.original == "Hola"
        assert r.translated == "Привет"
        assert r.latency_ms == 1500


# ------------------------------------------------------------------
# translate_text
# ------------------------------------------------------------------


def _make_mock_client(chunks: list[str], model: str = "google/gemini-3-flash") -> MagicMock:
    """Создаёт mock OpenClawClient с async generator для send_message_stream."""
    mock = MagicMock()
    mock.send_message_stream = MagicMock(return_value=_async_iter(chunks))
    mock.clear_session = MagicMock()
    mock._last_runtime_route = {"model": model}
    return mock


class TestTranslateText:
    @pytest.mark.asyncio
    async def test_basic_translation(self) -> None:
        mock_client = _make_mock_client(["Привет"])

        result = await translate_text("Hola", "es", "ru", openclaw_client=mock_client)

        assert result.translated == "Привет"
        assert result.src_lang == "es"
        assert result.tgt_lang == "ru"
        assert result.latency_ms >= 0
        assert result.model_id == "google/gemini-3-flash"
        # Wave 11: clear_session вызывается дважды (pre-clear для skip history + post-clear).
        assert mock_client.clear_session.call_count == 2
        mock_client.clear_session.assert_called_with("translator_mvp")

    @pytest.mark.asyncio
    async def test_strips_quotes(self) -> None:
        mock_client = _make_mock_client(['"Привет"'])
        result = await translate_text("Hola", "es", "ru", openclaw_client=mock_client)
        assert result.translated == "Привет"

    @pytest.mark.asyncio
    async def test_chunks_concatenated(self) -> None:
        mock_client = _make_mock_client(["При", "вет"])
        result = await translate_text("Hola", "es", "ru", openclaw_client=mock_client)
        assert result.translated == "Привет"

    @pytest.mark.asyncio
    async def test_force_cloud(self) -> None:
        mock_client = _make_mock_client(["ok"])
        await translate_text("test", "en", "ru", openclaw_client=mock_client)

        call_kwargs = mock_client.send_message_stream.call_args
        assert call_kwargs.kwargs.get("force_cloud") is True
        assert call_kwargs.kwargs.get("disable_tools") is True

    @pytest.mark.asyncio
    async def test_custom_chat_id(self) -> None:
        mock_client = _make_mock_client(["ok"])
        await translate_text(
            "test",
            "en",
            "ru",
            openclaw_client=mock_client,
            chat_id="custom_session",
        )
        # Wave 11: clear_session вызывается дважды (pre-clear + post-clear).
        assert mock_client.clear_session.call_count == 2
        mock_client.clear_session.assert_called_with("custom_session")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _async_iter(items: list[str]):
    """Хелпер: async iterator из списка строк."""
    for item in items:
        yield item


# ------------------------------------------------------------------
# _LANG_NAMES / _LANG_NAMES_TO — покрытие словарей
# ------------------------------------------------------------------


class TestLangNames:
    def test_key_languages_present(self) -> None:
        from src.core.translator_engine import _LANG_NAMES

        for code in ("es", "en", "ru", "fr", "de", "it", "pt", "uk"):
            assert code in _LANG_NAMES, f"Язык {code!r} отсутствует в _LANG_NAMES"

    def test_lang_names_to_present(self) -> None:
        from src.core.translator_engine import _LANG_NAMES_TO

        for code in ("es", "en", "ru", "fr", "de", "it", "pt", "uk"):
            assert code in _LANG_NAMES_TO, f"Язык {code!r} отсутствует в _LANG_NAMES_TO"

    def test_ru_values_differ_between_dicts(self) -> None:
        # «с русского» vs «на русский» — разные падежи
        from src.core.translator_engine import _LANG_NAMES, _LANG_NAMES_TO

        assert _LANG_NAMES["ru"] != _LANG_NAMES_TO["ru"]


# ------------------------------------------------------------------
# build_translation_prompt — дополнительные пары
# ------------------------------------------------------------------


class TestBuildTranslationPromptExtra:
    def test_fr_to_ru(self) -> None:
        from src.core.translator_engine import build_translation_prompt

        prompt = build_translation_prompt("Bonjour", "fr", "ru")
        assert "французского" in prompt
        assert "русский" in prompt
        assert "Bonjour" in prompt

    def test_de_to_en(self) -> None:
        from src.core.translator_engine import build_translation_prompt

        prompt = build_translation_prompt("Guten Tag", "de", "en")
        assert "немецкого" in prompt
        assert "английский" in prompt

    def test_prompt_no_extra_explanation(self) -> None:
        # Промпт не должен включать слово «объяснение»
        from src.core.translator_engine import build_translation_prompt

        prompt = build_translation_prompt("hello", "en", "ru")
        assert "объяснен" not in prompt.lower()


# ------------------------------------------------------------------
# TranslationResult — dataclass дополнительно
# ------------------------------------------------------------------


class TestTranslationResultExtra:
    def test_src_tgt_lang_stored(self) -> None:
        r = TranslationResult(
            original="Bonjour",
            translated="Привет",
            src_lang="fr",
            tgt_lang="ru",
            latency_ms=200,
            model_id="google/gemini-3-flash",
        )
        assert r.src_lang == "fr"
        assert r.tgt_lang == "ru"

    def test_model_id_stored(self) -> None:
        r = TranslationResult(
            original="x",
            translated="y",
            src_lang="en",
            tgt_lang="ru",
            latency_ms=0,
            model_id="test-model",
        )
        assert r.model_id == "test-model"


# ------------------------------------------------------------------
# translate_text — граничные случаи
# ------------------------------------------------------------------


class TestTranslateTextExtra:
    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        # Пустой ответ модели — translated должен быть пустой строкой
        mock_client = _make_mock_client([])
        result = await translate_text("Hola", "es", "ru", openclaw_client=mock_client)
        assert result.translated == ""
        assert result.original == "Hola"

    @pytest.mark.asyncio
    async def test_missing_route_returns_unknown(self) -> None:
        # Если _last_runtime_route не задан — model_id == "unknown"
        mock_client = _make_mock_client(["ok"])
        del mock_client._last_runtime_route
        result = await translate_text("hi", "en", "ru", openclaw_client=mock_client)
        assert result.model_id == "unknown"

    @pytest.mark.asyncio
    async def test_latency_non_negative(self) -> None:
        mock_client = _make_mock_client(["Привет"])
        result = await translate_text("Hello", "en", "ru", openclaw_client=mock_client)
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_preferred_model_in_call(self) -> None:
        # Убеждаемся, что в вызов передаётся flash-tier модель
        mock_client = _make_mock_client(["ok"])
        await translate_text("test", "en", "ru", openclaw_client=mock_client)
        call_kwargs = mock_client.send_message_stream.call_args.kwargs
        assert "gemini" in call_kwargs.get("preferred_model", "").lower()
