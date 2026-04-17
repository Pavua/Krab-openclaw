# -*- coding: utf-8 -*-
"""
Тесты обработчиков сообщений в KraabUserbot (src/userbot_bridge.py).

Покрываем pure/mockable логику:
  - _is_translator_active_for_chat
  - _extract_message_text
  - _is_command_like_text
  - _safe_reply_or_send_new (error handling)
  - _handle_translator_voice (logic flow через патчинг зависимостей)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Вспомогательные фабрики фейковых объектов
# ---------------------------------------------------------------------------


def _make_bot(**kwargs) -> MagicMock:
    """Создаёт минимальный mock KraabUserbot с нужными атрибутами."""
    bot = MagicMock()

    # Состояние переводчика по умолчанию — неактивное
    default_state: dict = {
        "session_status": "idle",
        "translation_muted": False,
        "active_chats": [],
    }
    bot.get_translator_session_state.return_value = {**default_state, **kwargs.get("state", {})}
    bot.get_translator_runtime_profile.return_value = kwargs.get(
        "profile", {"language_pair": "es-ru"}
    )
    bot.update_translator_session_state = MagicMock(return_value=None)
    bot._safe_reply_or_send_new = AsyncMock(return_value=MagicMock())
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock(return_value=MagicMock())
    return bot


def _make_message(text: str = "", caption: str = "", chat_id: int = 100) -> MagicMock:
    """Создаёт минимальный mock Pyrogram Message."""
    msg = MagicMock()
    msg.text = text or None
    msg.caption = caption or None
    msg.chat = SimpleNamespace(id=chat_id)
    msg.id = 42
    msg.reply = AsyncMock(return_value=MagicMock())
    return msg


# ---------------------------------------------------------------------------
# Тесты _is_translator_active_for_chat
# ---------------------------------------------------------------------------


class TestIsTranslatorActiveForChat:
    """Проверяет логику активации переводчика для конкретного чата."""

    def _call(self, state: dict, chat_id) -> bool:
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = MagicMock(spec=KraabUserbot)
        bot.get_translator_session_state.return_value = state
        return KraabUserbot._is_translator_active_for_chat(bot, chat_id)

    def test_inactive_when_session_idle(self):
        """Сессия idle → не активна ни для какого чата."""
        state = {"session_status": "idle", "translation_muted": False, "active_chats": []}
        assert self._call(state, 123) is False

    def test_inactive_when_muted(self):
        """Сессия active, но muted → не активна."""
        state = {"session_status": "active", "translation_muted": True, "active_chats": []}
        assert self._call(state, 123) is False

    def test_active_for_all_when_active_chats_empty(self):
        """active_chats пуст + active → переводчик активен для всех чатов."""
        state = {"session_status": "active", "translation_muted": False, "active_chats": []}
        assert self._call(state, 999) is True

    def test_active_for_listed_chat(self):
        """Чат есть в active_chats → активен."""
        state = {"session_status": "active", "translation_muted": False, "active_chats": [100, 200]}
        assert self._call(state, 100) is True

    def test_inactive_for_unlisted_chat(self):
        """Чат НЕ в active_chats → не активен."""
        state = {"session_status": "active", "translation_muted": False, "active_chats": [100]}
        assert self._call(state, 999) is False

    def test_chat_id_as_string_matches_int(self):
        """Сравнение str/int chat_id работает корректно (всё приводится к str)."""
        state = {"session_status": "active", "translation_muted": False, "active_chats": [100]}
        assert self._call(state, "100") is True

    def test_missing_keys_treated_as_inactive(self):
        """Если ключи отсутствуют — считаем неактивным (нет session_status)."""
        assert self._call({}, 1) is False


# ---------------------------------------------------------------------------
# Тесты _extract_message_text
# ---------------------------------------------------------------------------


class TestExtractMessageText:
    """Проверяет унифицированное извлечение текста из сообщения."""

    def _call(self, msg) -> str:
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        return KraabUserbot._extract_message_text(msg)

    def test_returns_text_field(self):
        msg = _make_message(text="Привет, Краб!")
        assert self._call(msg) == "Привет, Краб!"

    def test_falls_back_to_caption(self):
        """Если text нет — берём caption (у медиасообщений)."""
        msg = _make_message(caption="Подпись к фото")
        assert self._call(msg) == "Подпись к фото"

    def test_empty_string_when_no_text_or_caption(self):
        """Нет ни text, ни caption → пустая строка."""
        msg = MagicMock()
        msg.text = None
        msg.caption = None
        assert self._call(msg) == ""

    def test_text_takes_priority_over_caption(self):
        """text главнее caption."""
        msg = MagicMock()
        msg.text = "Текст"
        msg.caption = "Подпись"
        assert self._call(msg) == "Текст"


# ---------------------------------------------------------------------------
# Тесты _is_command_like_text
# ---------------------------------------------------------------------------


class TestIsCommandLikeText:
    """Проверяет определение служебных команд."""

    def _call(self, text: str) -> bool:
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        return KraabUserbot._is_command_like_text(text)

    def test_exclamation_mark_prefix(self):
        assert self._call("!swarm traders") is True

    def test_slash_prefix(self):
        assert self._call("/start") is True

    def test_dot_prefix(self):
        assert self._call(".translate") is True

    def test_regular_text_not_command(self):
        assert self._call("Просто текст") is False

    def test_empty_string_not_command(self):
        assert self._call("") is False

    def test_leading_whitespace_then_command(self):
        """Пробел в начале — lstrip → всё равно команда."""
        assert self._call("  !краб") is True

    def test_question_mark_not_command(self):
        assert self._call("?help") is False


# ---------------------------------------------------------------------------
# Тесты _safe_reply_or_send_new
# ---------------------------------------------------------------------------


class TestSafeReplyOrSendNew:
    """Проверяет fallback логику при ошибке reply."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        """При успешном reply возвращает результат."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = MagicMock()  # без spec — иначе .client недоступен
        sent = MagicMock()
        msg = _make_message(text="hi")
        msg.reply = AsyncMock(return_value=sent)

        # mock_run должен await корутину, которую вернёт lambda
        async def mock_run(chat_id, fn):
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=mock_run)
            result = await KraabUserbot._safe_reply_or_send_new(bot, msg, "Ответ")

        assert result is sent

    @pytest.mark.asyncio
    async def test_fallback_to_send_message_on_reply_error(self):
        """При ошибке reply → fallback на send_message."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = MagicMock()  # без spec
        fallback_msg = MagicMock()
        bot.client.send_message = AsyncMock(return_value=fallback_msg)

        msg = _make_message(text="hi", chat_id=555)
        msg.reply = AsyncMock(side_effect=Exception("REPLY_FAILED"))

        async def mock_run(chat_id, fn):
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=mock_run)
            await KraabUserbot._safe_reply_or_send_new(bot, msg, "Текст fallback")

        # После ошибки должен быть вызван send_message
        bot.client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_text_becomes_ellipsis(self):
        """Пустой текст превращается в '…' (не падаем при пустом ответе)."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = MagicMock()  # без spec
        sent = MagicMock()
        msg = _make_message(text="", chat_id=10)

        captured_text = []

        async def mock_run(chat_id, fn):
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result

        msg.reply = AsyncMock(side_effect=Exception("reply err"))

        async def capture_send(chat_id, text, **_kwargs):
            captured_text.append(text)
            return sent

        bot.client.send_message = capture_send

        with patch("src.userbot_bridge._telegram_send_queue") as mock_q:
            mock_q.run = AsyncMock(side_effect=mock_run)
            await KraabUserbot._safe_reply_or_send_new(bot, msg, "")

        # Текст не должен быть пустой строкой — '…' заменяет ""
        assert captured_text and captured_text[0] != ""


# ---------------------------------------------------------------------------
# Тесты _handle_translator_voice (логика flow)
# ---------------------------------------------------------------------------


class TestHandleTranslatorVoice:
    """Проверяет логику pipeline перевода голосовых заметок."""

    def _make_bot_for_translator(self, profile=None, state=None):
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = MagicMock(spec=KraabUserbot)
        bot.get_translator_runtime_profile.return_value = profile or {"language_pair": "es-ru"}
        bot.get_translator_session_state.return_value = state or {"session_status": "active"}
        bot.update_translator_session_state = MagicMock()
        bot._safe_reply_or_send_new = AsyncMock(return_value=MagicMock())
        return bot

    @pytest.mark.asyncio
    async def test_returns_false_when_language_not_detected(self):
        """Если язык не определён → False (идём в обычный LLM)."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = self._make_bot_for_translator()
        msg = _make_message()

        # Патчим импорт внутри метода через sys.modules
        import sys

        fake_ld = MagicMock()
        fake_ld.detect_language = MagicMock(return_value=None)
        fake_ld.resolve_translation_pair = MagicMock(return_value=("es", "ru"))
        fake_te = MagicMock()

        sys.modules["src.core.language_detect"] = fake_ld
        sys.modules["src.core.translator_engine"] = fake_te

        result = await KraabUserbot._handle_translator_voice(bot, msg, "texto", 100)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_same_language(self):
        """src_lang == tgt_lang → False, переводить нечего."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = self._make_bot_for_translator()
        msg = _make_message()

        import sys

        fake_ld = MagicMock()
        fake_ld.detect_language = MagicMock(return_value="ru")
        # resolve возвращает одинаковые языки
        fake_ld.resolve_translation_pair = MagicMock(return_value=("ru", "ru"))
        fake_te = MagicMock()
        sys.modules["src.core.language_detect"] = fake_ld
        sys.modules["src.core.translator_engine"] = fake_te

        result = await KraabUserbot._handle_translator_voice(bot, msg, "Текст на русском", 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_translate_raises(self):
        """Исключение в translate_text → False (fallback к LLM, не crash)."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = self._make_bot_for_translator()
        msg = _make_message()

        import sys

        fake_ld = MagicMock()
        fake_ld.detect_language = MagicMock(return_value="es")
        fake_ld.resolve_translation_pair = MagicMock(return_value=("es", "ru"))
        fake_te = MagicMock()
        fake_te.translate_text = AsyncMock(side_effect=RuntimeError("сеть недоступна"))
        sys.modules["src.core.language_detect"] = fake_ld
        sys.modules["src.core.translator_engine"] = fake_te

        with patch("src.userbot_bridge.openclaw_client", MagicMock(), create=True):
            result = await KraabUserbot._handle_translator_voice(bot, msg, "Hola mundo", 100)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_and_replies_on_success(self):
        """Успешный перевод → True + вызов _safe_reply_or_send_new."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = self._make_bot_for_translator()
        msg = _make_message()

        translation_result = MagicMock()
        translation_result.translated = "Привет, мир"
        translation_result.original = "Hola mundo"
        translation_result.latency_ms = 120
        translation_result.model_id = "gemini-3-flash"

        import sys

        fake_ld = MagicMock()
        fake_ld.detect_language = MagicMock(return_value="es")
        fake_ld.resolve_translation_pair = MagicMock(return_value=("es", "ru"))
        fake_te = MagicMock()
        fake_te.translate_text = AsyncMock(return_value=translation_result)
        sys.modules["src.core.language_detect"] = fake_ld
        sys.modules["src.core.translator_engine"] = fake_te

        bot.get_translator_session_state.return_value = {
            "session_status": "active",
            "stats": {"total_translations": 0, "total_latency_ms": 0},
        }

        with patch("src.userbot_bridge.openclaw_client", MagicMock(), create=True):
            result = await KraabUserbot._handle_translator_voice(bot, msg, "Hola mundo", 100)

        assert result is True
        bot._safe_reply_or_send_new.assert_called_once()
        # Проверяем что reply содержит оба языка
        call_args = bot._safe_reply_or_send_new.call_args
        reply_text = call_args[0][1]
        assert "es" in reply_text and "ru" in reply_text

    @pytest.mark.asyncio
    async def test_returns_false_when_translated_is_empty(self):
        """Перевод вернул пустой результат → False."""
        from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

        bot = self._make_bot_for_translator()
        msg = _make_message()

        empty_result = MagicMock()
        empty_result.translated = ""  # пустой перевод

        import sys

        fake_ld = MagicMock()
        fake_ld.detect_language = MagicMock(return_value="es")
        fake_ld.resolve_translation_pair = MagicMock(return_value=("es", "ru"))
        fake_te = MagicMock()
        fake_te.translate_text = AsyncMock(return_value=empty_result)
        sys.modules["src.core.language_detect"] = fake_ld
        sys.modules["src.core.translator_engine"] = fake_te

        with patch("src.userbot_bridge.openclaw_client", MagicMock(), create=True):
            result = await KraabUserbot._handle_translator_voice(bot, msg, "Hola", 100)

        assert result is False
