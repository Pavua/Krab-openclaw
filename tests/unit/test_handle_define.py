# -*- coding: utf-8 -*-
"""
Тесты команды !define — определение слова/термина через AI.
Охватывает: _parse_define_args, _build_define_prompt, handle_define (integration mock).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.handlers.command_handlers import (
    _build_define_prompt,
    _parse_define_args,
)


# ---------------------------------------------------------------------------
# _parse_define_args
# ---------------------------------------------------------------------------


class TestParseDefineArgs:
    """Разбор аргументов !define."""

    def test_пустая_строка(self):
        term, lang, detailed = _parse_define_args("")
        assert term == ""
        assert lang == "ru"
        assert detailed is False

    def test_только_слово(self):
        term, lang, detailed = _parse_define_args("Python")
        assert term == "Python"
        assert lang == "ru"
        assert detailed is False

    def test_слово_с_en(self):
        term, lang, detailed = _parse_define_args("Python en")
        assert term == "Python"
        assert lang == "en"
        assert detailed is False

    def test_слово_english(self):
        term, lang, detailed = _parse_define_args("entropy english")
        assert term == "entropy"
        assert lang == "en"
        assert detailed is False

    def test_слово_подробно(self):
        term, lang, detailed = _parse_define_args("энтропия подробно")
        assert term == "энтропия"
        assert lang == "ru"
        assert detailed is True

    def test_слово_detailed(self):
        term, lang, detailed = _parse_define_args("entropy detailed")
        assert term == "entropy"
        # lang — ru по умолчанию, "detailed" — модификатор, не языковой маркер
        assert lang == "ru"
        assert detailed is True

    def test_слово_en_подробно(self):
        term, lang, detailed = _parse_define_args("recursion en подробно")
        assert term == "recursion"
        assert lang == "en"
        assert detailed is True

    def test_порядок_не_важен(self):
        """Модификаторы можно ставить в любом порядке."""
        term, lang, detailed = _parse_define_args("подробно recursion en")
        assert term == "recursion"
        assert lang == "en"
        assert detailed is True

    def test_многосложный_термин(self):
        term, lang, detailed = _parse_define_args("машинное обучение")
        assert term == "машинное обучение"
        assert lang == "ru"
        assert detailed is False

    def test_многосложный_термин_с_en(self):
        term, lang, detailed = _parse_define_args("machine learning en")
        assert term == "machine learning"
        assert lang == "en"
        assert detailed is False

    def test_полностью_keyword(self):
        term, lang, detailed = _parse_define_args("квант полностью")
        assert term == "квант"
        assert detailed is True

    def test_расширенно_keyword(self):
        term, lang, detailed = _parse_define_args("квант расширенно")
        assert term == "квант"
        assert detailed is True

    def test_full_keyword(self):
        term, lang, detailed = _parse_define_args("qubit full")
        assert term == "qubit"
        assert detailed is True

    def test_англ_keyword(self):
        term, lang, detailed = _parse_define_args("qubit англ")
        assert term == "qubit"
        assert lang == "en"

    def test_регистр_не_важен_для_модификаторов(self):
        term, lang, detailed = _parse_define_args("Python EN ПОДРОБНО")
        assert term == "Python"
        assert lang == "en"
        assert detailed is True

    def test_только_модификатор_без_слова(self):
        """Только модификатор — term пустой."""
        term, lang, detailed = _parse_define_args("en")
        assert term == ""
        assert lang == "en"


# ---------------------------------------------------------------------------
# _build_define_prompt
# ---------------------------------------------------------------------------


class TestBuildDefinePrompt:
    """Формирование промпта для запроса определения."""

    def test_краткий_рус(self):
        prompt = _build_define_prompt("энтропия", "ru", False)
        assert "энтропия" in prompt
        assert "краткое" in prompt
        assert "русском" in prompt

    def test_подробный_рус(self):
        prompt = _build_define_prompt("энтропия", "ru", True)
        assert "энтропия" in prompt
        assert "развёрнутое" in prompt
        assert "русском" in prompt

    def test_краткий_en(self):
        prompt = _build_define_prompt("entropy", "en", False)
        assert "entropy" in prompt
        assert "brief" in prompt
        assert "English" in prompt

    def test_подробный_en(self):
        prompt = _build_define_prompt("entropy", "en", True)
        assert "entropy" in prompt
        assert "detailed" in prompt
        assert "English" in prompt

    def test_промпт_непустой(self):
        """Промпт всегда непустой для любой комбинации параметров."""
        for lang in ("ru", "en"):
            for detailed in (True, False):
                result = _build_define_prompt("test", lang, detailed)
                assert len(result) > 10

    def test_термин_обёрнут_в_кавычки(self):
        """Термин должен быть внутри кавычек в промпте."""
        prompt = _build_define_prompt("Python", "ru", False)
        assert "«Python»" in prompt

    def test_подробный_содержит_этимологию(self):
        """Подробный режим упоминает этимологию."""
        prompt_ru = _build_define_prompt("слово", "ru", True)
        assert "этимологию" in prompt_ru or "etymology" in prompt_ru
        prompt_en = _build_define_prompt("word", "en", True)
        assert "etymology" in prompt_en


# ---------------------------------------------------------------------------
# handle_define — integration tests с mock
# ---------------------------------------------------------------------------


class TestHandleDefine:
    """Интеграционные тесты handle_define с мок-объектами."""

    def _make_message(self, text: str, chat_id: int = 100) -> object:
        """Создаёт минимальный mock сообщения Telegram."""
        import types

        msg = types.SimpleNamespace()
        msg.text = text
        msg.chat = types.SimpleNamespace(id=chat_id)
        msg.reply_to_message = None
        msg.from_user = types.SimpleNamespace(id=1, first_name="Test", username="test")

        reply_mock = types.SimpleNamespace()
        reply_mock.edit = AsyncMock()
        msg.reply = AsyncMock(return_value=reply_mock)

        return msg, reply_mock

    def _make_bot(self, command_args: str) -> object:
        """Создаёт минимальный mock бота."""
        import types

        bot = types.SimpleNamespace()
        bot._get_command_args = lambda m: command_args
        return bot

    @pytest.mark.asyncio
    async def test_успешное_определение_краткое(self, monkeypatch):
        """Краткое определение — вызывает stream, редактирует статус."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Python — высокоуровневый язык программирования."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define Python", chat_id=42)
        bot = self._make_bot("Python")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        msg.reply.assert_awaited_once()
        reply_mock.edit.assert_awaited_once()
        edited_text = reply_mock.edit.call_args[0][0]
        assert "Python" in edited_text
        assert "📖" in edited_text

    @pytest.mark.asyncio
    async def test_определение_en(self, monkeypatch):
        """Запрос en — заголовок содержит (EN)."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Python is a high-level programming language."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define Python en", chat_id=42)
        bot = self._make_bot("Python en")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert "(EN)" in edited_text

    @pytest.mark.asyncio
    async def test_определение_подробно(self, monkeypatch):
        """Подробный режим — заголовок содержит (подробно)."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Развёрнутое определение Python..."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define Python подробно", chat_id=42)
        bot = self._make_bot("Python подробно")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert "подробно" in edited_text

    @pytest.mark.asyncio
    async def test_session_id_изолирован(self, monkeypatch):
        """session_id должен содержать chat_id, а не основной чат."""
        from src.handlers import command_handlers

        captured_chat_id: list[str] = []

        async def fake_stream(*args, **kwargs):
            captured_chat_id.append(kwargs.get("chat_id", ""))
            yield "Определение."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, _ = self._make_message("!define тест", chat_id=999)
        bot = self._make_bot("тест")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        assert len(captured_chat_id) == 1
        assert captured_chat_id[0] == "define_999"

    @pytest.mark.asyncio
    async def test_disable_tools_true(self, monkeypatch):
        """disable_tools должен быть True — инструменты отключены."""
        from src.handlers import command_handlers

        captured_kwargs: list[dict] = []

        async def fake_stream(*args, **kwargs):
            captured_kwargs.append(kwargs)
            yield "Определение."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, _ = self._make_message("!define тест", chat_id=5)
        bot = self._make_bot("тест")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        assert captured_kwargs[0].get("disable_tools") is True

    @pytest.mark.asyncio
    async def test_подробно_max_tokens_больше(self, monkeypatch):
        """Режим подробно — max_output_tokens больше чем краткий."""
        from src.handlers import command_handlers

        tokens_brief: list[int] = []
        tokens_detailed: list[int] = []

        async def fake_brief(*args, **kwargs):
            tokens_brief.append(kwargs.get("max_output_tokens", 0))
            yield "Краткое."

        async def fake_detailed(*args, **kwargs):
            tokens_detailed.append(kwargs.get("max_output_tokens", 0))
            yield "Подробное."

        msg1, _ = self._make_message("!define тест", chat_id=1)
        bot1 = self._make_bot("тест")
        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_brief)

        from src.handlers.command_handlers import handle_define

        await handle_define(bot1, msg1)

        msg2, _ = self._make_message("!define тест подробно", chat_id=2)
        bot2 = self._make_bot("тест подробно")
        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_detailed)

        await handle_define(bot2, msg2)

        assert tokens_detailed[0] > tokens_brief[0]

    @pytest.mark.asyncio
    async def test_пустой_ответ_от_модели_показывает_ошибку(self, monkeypatch):
        """Если модель вернула пустую строку — статус-сообщение содержит ошибку."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            # Генератор пустой
            return
            yield  # noqa: unreachable

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define тест", chat_id=3)
        bot = self._make_bot("тест")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert "❌" in edited_text

    @pytest.mark.asyncio
    async def test_обрезка_длинного_ответа(self, monkeypatch):
        """Очень длинный ответ — обрезается до 4000 символов."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "A" * 5000

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define тест", chat_id=4)
        bot = self._make_bot("тест")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert len(edited_text) <= 4000

    @pytest.mark.asyncio
    async def test_нет_аргументов_рейзит_error(self):
        """Без аргументов — UserInputError."""
        from src.core.exceptions import UserInputError

        msg, _ = self._make_message("!define", chat_id=1)
        msg.reply_to_message = None
        bot = self._make_bot("")

        from src.handlers.command_handlers import handle_define

        with pytest.raises(UserInputError):
            await handle_define(bot, msg)

    @pytest.mark.asyncio
    async def test_только_модификатор_без_слова_рейзит_error(self):
        """Только 'en' без термина — UserInputError."""
        from src.core.exceptions import UserInputError

        msg, _ = self._make_message("!define en", chat_id=1)
        msg.reply_to_message = None
        bot = self._make_bot("en")

        from src.handlers.command_handlers import handle_define

        with pytest.raises(UserInputError):
            await handle_define(bot, msg)

    @pytest.mark.asyncio
    async def test_текст_из_reply(self, monkeypatch):
        """Если аргументов нет, но есть reply — берём текст из reply."""
        import types

        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Определение из reply."

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        reply_msg = types.SimpleNamespace(text="энтропия")
        msg, reply_mock = self._make_message("!define", chat_id=10)
        msg.reply_to_message = reply_msg
        bot = self._make_bot("")  # пустые аргументы

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert "энтропия" in edited_text

    @pytest.mark.asyncio
    async def test_ошибка_stream_показывает_ошибку(self, monkeypatch):
        """Исключение в stream — статус-сообщение содержит ❌."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            raise RuntimeError("connection timeout")
            yield  # noqa: unreachable

        monkeypatch.setattr(
            command_handlers.openclaw_client, "send_message_stream", fake_stream
        )

        msg, reply_mock = self._make_message("!define тест", chat_id=6)
        bot = self._make_bot("тест")

        from src.handlers.command_handlers import handle_define

        await handle_define(bot, msg)

        edited_text = reply_mock.edit.call_args[0][0]
        assert "❌" in edited_text
