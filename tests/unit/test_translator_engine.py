# -*- coding: utf-8 -*-
"""
Тесты для translator engine модулей:
  - src/core/language_detect.py
  - src/core/translator_engine.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.language_detect import detect_language, resolve_translation_pair
from src.core.translator_engine import (
    TranslationResult,
    build_translation_prompt,
    translate_text,
)

# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    """Тесты определения языка текста."""

    def test_detect_russian(self):
        """Детектирует русский текст корректно."""
        result = detect_language("Привет, как дела? Сегодня хорошая погода.")
        assert result == "ru"

    def test_detect_spanish(self):
        """Детектирует испанский текст корректно."""
        result = detect_language("Hola, ¿cómo estás? El tiempo es muy bueno hoy.")
        assert result == "es"

    def test_detect_english(self):
        """Детектирует английский текст корректно."""
        result = detect_language("Hello, how are you? The weather is great today.")
        assert result == "en"

    def test_empty_string_returns_empty(self):
        """Пустая строка возвращает пустой код языка."""
        result = detect_language("")
        assert result == ""

    def test_none_returns_empty(self):
        """None возвращает пустой код языка."""
        result = detect_language(None)  # type: ignore[arg-type]
        assert result == ""

    def test_too_short_text_returns_empty(self):
        """Слишком короткий текст (< 5 символов) возвращает пустую строку."""
        result = detect_language("hi")
        assert result == ""

    def test_whitespace_only_returns_empty(self):
        """Строка из пробелов возвращает пустую строку."""
        result = detect_language("    ")
        assert result == ""

    def test_exactly_min_length_boundary(self):
        """Текст ровно на границе минимальной длины (5 символов) не падает."""
        # Не проверяем конкретный язык — главное что не кидает исключение
        result = detect_language("hello")
        assert isinstance(result, str)

    def test_detect_returns_string(self):
        """Функция всегда возвращает строку."""
        for text in ["", "  ", "test text here", "Текст на русском языке"]:
            assert isinstance(detect_language(text), str)


# ---------------------------------------------------------------------------
# resolve_translation_pair
# ---------------------------------------------------------------------------


class TestResolveTranslationPair:
    """Тесты резолва пары перевода."""

    def test_es_detected_in_es_ru_pair(self):
        """Если detected=es и pair=es-ru, переводим es→ru."""
        src, tgt = resolve_translation_pair("es", "es-ru")
        assert src == "es"
        assert tgt == "ru"

    def test_ru_detected_in_es_ru_pair(self):
        """Если detected=ru и pair=es-ru, переводим ru→es."""
        src, tgt = resolve_translation_pair("ru", "es-ru")
        assert src == "ru"
        assert tgt == "es"

    def test_unknown_lang_in_es_ru_pair(self):
        """Если язык не в паре — переводим на второй язык пары (ru)."""
        src, tgt = resolve_translation_pair("fr", "es-ru")
        assert src == "fr"
        assert tgt == "ru"

    def test_auto_detect_non_russian(self):
        """auto-detect: не-русский → русский."""
        src, tgt = resolve_translation_pair("es", "auto-detect")
        assert src == "es"
        assert tgt == "ru"

    def test_auto_detect_russian(self):
        """auto-detect: русский → английский."""
        src, tgt = resolve_translation_pair("ru", "auto-detect")
        assert src == "ru"
        assert tgt == "en"

    def test_auto_detect_english(self):
        """auto-detect: английский → русский."""
        src, tgt = resolve_translation_pair("en", "auto-detect")
        assert src == "en"
        assert tgt == "ru"

    def test_invalid_pair_format(self):
        """Некорректный формат пары — fallback на ru как target."""
        src, tgt = resolve_translation_pair("es", "invalidpair")
        assert tgt == "ru"

    def test_en_ru_pair_with_en(self):
        """Пара en-ru: detected=en → en→ru."""
        src, tgt = resolve_translation_pair("en", "en-ru")
        assert src == "en"
        assert tgt == "ru"


# ---------------------------------------------------------------------------
# build_translation_prompt
# ---------------------------------------------------------------------------


class TestBuildTranslationPrompt:
    """Тесты построения промпта для перевода."""

    def test_prompt_contains_text(self):
        """Промпт содержит исходный текст."""
        prompt = build_translation_prompt("Hola mundo", "es", "ru")
        assert "Hola mundo" in prompt

    def test_prompt_mentions_src_lang(self):
        """Промпт содержит название исходного языка."""
        prompt = build_translation_prompt("text", "es", "ru")
        assert "испанского" in prompt

    def test_prompt_mentions_tgt_lang(self):
        """Промпт содержит название целевого языка."""
        prompt = build_translation_prompt("text", "es", "ru")
        assert "русский" in prompt

    def test_prompt_unknown_lang_uses_code(self):
        """Промпт для неизвестного языка использует код напрямую."""
        prompt = build_translation_prompt("text", "xx", "yy")
        assert "xx" in prompt
        assert "yy" in prompt


# ---------------------------------------------------------------------------
# translate_text (async, с моком openclaw_client)
# ---------------------------------------------------------------------------


class TestTranslateText:
    """Тесты translate_text с мокнутым OpenClaw клиентом."""

    def _make_client(self, chunks: list[str], model: str = "gemini-flash") -> MagicMock:
        """Хелпер: создаёт мок клиента, который стримит заданные чанки."""
        client = MagicMock()

        async def _fake_stream(**kwargs):
            for chunk in chunks:
                yield chunk

        client.send_message_stream = MagicMock(side_effect=_fake_stream)
        client._last_runtime_route = {"model": model}
        client.clear_session = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_success_returns_translation_result(self):
        """Успешный перевод возвращает TranslationResult с корректными полями."""
        client = self._make_client(["Привет ", "мир"])
        result = await translate_text("Hello world", "en", "ru", openclaw_client=client)
        assert isinstance(result, TranslationResult)
        assert result.translated == "Привет мир"
        assert result.original == "Hello world"
        assert result.src_lang == "en"
        assert result.tgt_lang == "ru"

    @pytest.mark.asyncio
    async def test_model_id_extracted_from_route(self):
        """model_id берётся из _last_runtime_route клиента."""
        client = self._make_client(["Hola"], model="google/gemini-3-flash-preview")
        result = await translate_text("Hello", "en", "es", openclaw_client=client)
        assert result.model_id == "google/gemini-3-flash-preview"

    @pytest.mark.asyncio
    async def test_quotes_stripped_from_translation(self):
        """Кавычки вокруг перевода автоматически убираются."""
        client = self._make_client(['"Привет мир"'])
        result = await translate_text("Hello world", "en", "ru", openclaw_client=client)
        assert result.translated == "Привет мир"

    @pytest.mark.asyncio
    async def test_clear_session_called_after_translation(self):
        """clear_session вызывается после перевода для очистки истории."""
        client = self._make_client(["Hola"])
        await translate_text("Hello", "en", "es", openclaw_client=client)
        client.clear_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_chat_id_passed_to_stream(self):
        """Кастомный chat_id передаётся в send_message_stream."""
        client = self._make_client(["ok"])
        await translate_text("text", "en", "ru", openclaw_client=client, chat_id="custom_id")
        call_kwargs = client.send_message_stream.call_args
        assert call_kwargs.kwargs.get("chat_id") == "custom_id"

    @pytest.mark.asyncio
    async def test_clear_session_error_does_not_raise(self):
        """Ошибка в clear_session не роняет translate_text."""
        client = self._make_client(["Resultado"])
        client.clear_session.side_effect = RuntimeError("session error")
        # Не должно кидать исключение
        result = await translate_text("Result", "en", "es", openclaw_client=client)
        assert result.translated == "Resultado"

    @pytest.mark.asyncio
    async def test_missing_runtime_route_returns_unknown(self):
        """Если _last_runtime_route отсутствует — model_id = 'unknown'."""
        client = self._make_client(["текст"])
        del client._last_runtime_route
        result = await translate_text("text", "en", "ru", openclaw_client=client)
        assert result.model_id == "unknown"

    @pytest.mark.asyncio
    async def test_latency_ms_is_non_negative(self):
        """latency_ms всегда >= 0."""
        client = self._make_client(["translation"])
        result = await translate_text("hello", "en", "ru", openclaw_client=client)
        assert result.latency_ms >= 0
