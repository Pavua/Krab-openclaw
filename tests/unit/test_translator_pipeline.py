# -*- coding: utf-8 -*-
"""
Тесты для translator MVP pipeline — integration уровень.

Покрываем:
1) _is_translator_active_for_chat — routing logic
2) _handle_translator_voice — перевод voice note transcript
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


def _make_bot_stub(
    session_status: str = "idle",
    active_chats: list | None = None,
    translation_muted: bool = False,
    language_pair: str = "es-ru",
) -> KraabUserbot:
    """Минимальный bot stub для translator тестов."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.current_role = "default"

    # Мокаем translator state/profile
    bot.get_translator_session_state = MagicMock(
        return_value={
            "session_status": session_status,
            "translation_muted": translation_muted,
            "active_chats": active_chats or [],
            "stats": {"total_translations": 0, "total_latency_ms": 0},
        }
    )
    bot.get_translator_runtime_profile = MagicMock(
        return_value={
            "language_pair": language_pair,
        }
    )
    bot.update_translator_session_state = MagicMock()
    bot._safe_reply_or_send_new = AsyncMock()
    bot.openclaw = MagicMock()
    return bot


# ------------------------------------------------------------------
# _is_translator_active_for_chat
# ------------------------------------------------------------------


class TestIsTranslatorActiveForChat:
    def test_idle_session(self) -> None:
        bot = _make_bot_stub(session_status="idle")
        assert bot._is_translator_active_for_chat(12345) is False

    def test_active_session_no_chat_filter(self) -> None:
        # Семантика opt-in: пустой active_chats → translator неактивен (Wave 11).
        # Ранее был fallback "активен для всех" — он нарушал opt-in и убран.
        bot = _make_bot_stub(session_status="active")
        assert bot._is_translator_active_for_chat(12345) is False

    def test_active_session_chat_in_list(self) -> None:
        bot = _make_bot_stub(session_status="active", active_chats=["12345"])
        assert bot._is_translator_active_for_chat(12345) is True
        assert bot._is_translator_active_for_chat("12345") is True

    def test_active_session_chat_not_in_list(self) -> None:
        bot = _make_bot_stub(session_status="active", active_chats=["99999"])
        assert bot._is_translator_active_for_chat(12345) is False

    def test_muted_session(self) -> None:
        bot = _make_bot_stub(session_status="active", translation_muted=True)
        assert bot._is_translator_active_for_chat(12345) is False


# ------------------------------------------------------------------
# _handle_translator_voice
# ------------------------------------------------------------------


class TestHandleTranslatorVoice:
    @pytest.mark.asyncio
    async def test_translates_spanish_to_russian(self) -> None:
        bot = _make_bot_stub(session_status="active", language_pair="es-ru")

        with (
            patch("src.core.language_detect.detect_language", return_value="es"),
            patch("src.core.language_detect.resolve_translation_pair", return_value=("es", "ru")),
            patch(
                "src.core.translator_engine.translate_text", new_callable=AsyncMock
            ) as mock_translate,
        ):
            mock_translate.return_value = SimpleNamespace(
                original="Hola mundo",
                translated="Привет мир",
                src_lang="es",
                tgt_lang="ru",
                latency_ms=1500,
                model_id="google/gemini-3-flash",
            )

            result = await bot._handle_translator_voice(MagicMock(), "Hola mundo", 12345)

        assert result is True
        bot._safe_reply_or_send_new.assert_called_once()
        reply_text = bot._safe_reply_or_send_new.call_args[0][1]
        assert "es→ru" in reply_text
        assert "Привет мир" in reply_text
        bot.update_translator_session_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_if_language_not_detected(self) -> None:
        bot = _make_bot_stub(session_status="active")

        with patch("src.core.language_detect.detect_language", return_value=""):
            result = await bot._handle_translator_voice(MagicMock(), "...", 12345)

        assert result is False
        bot._safe_reply_or_send_new.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_if_same_language(self) -> None:
        bot = _make_bot_stub(session_status="active", language_pair="es-ru")

        with (
            patch("src.core.language_detect.detect_language", return_value="es"),
            patch("src.core.language_detect.resolve_translation_pair", return_value=("es", "es")),
        ):
            result = await bot._handle_translator_voice(MagicMock(), "Hola", 12345)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_translation_error(self) -> None:
        bot = _make_bot_stub(session_status="active", language_pair="es-ru")

        with (
            patch("src.core.language_detect.detect_language", return_value="es"),
            patch("src.core.language_detect.resolve_translation_pair", return_value=("es", "ru")),
            patch(
                "src.core.translator_engine.translate_text",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API down"),
            ),
        ):
            result = await bot._handle_translator_voice(MagicMock(), "Hola", 12345)

        assert result is False  # fallback к обычному LLM
