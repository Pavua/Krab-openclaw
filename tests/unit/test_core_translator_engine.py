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
        mock_client.clear_session.assert_called_once_with("translator_mvp")

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
        mock_client.clear_session.assert_called_once_with("custom_session")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _async_iter(items: list[str]):
    """Хелпер: async iterator из списка строк."""
    for item in items:
        yield item
