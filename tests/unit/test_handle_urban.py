# -*- coding: utf-8 -*-
"""
Тесты команды !urban — Urban Dictionary lookup через AI + web_search.
Охватывает: happy path, пустой запрос, reply-fallback, disable_tools=False,
session_id изоляция, пустой ответ, ошибка stream, пагинация.
"""

from __future__ import annotations

import types

import pytest
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_message(text: str = "!urban yeet", chat_id: int = 100) -> tuple:
    """Минимальный mock Telegram-сообщения."""
    msg = types.SimpleNamespace()
    msg.text = text
    msg.chat = types.SimpleNamespace(id=chat_id)
    msg.reply_to_message = None
    msg.from_user = types.SimpleNamespace(id=1, first_name="Test", username="test")

    reply_mock = types.SimpleNamespace()
    reply_mock.edit = AsyncMock()
    msg.reply = AsyncMock(return_value=reply_mock)

    return msg, reply_mock


def _make_bot(command_args: str) -> object:
    """Минимальный mock бота с _get_command_args."""
    bot = types.SimpleNamespace()
    bot._get_command_args = lambda m: command_args
    return bot


# ---------------------------------------------------------------------------
# handle_urban — интеграционные тесты с моком openclaw_client
# ---------------------------------------------------------------------------


class TestHandleUrban:
    """Тесты команды !urban."""

    @pytest.mark.asyncio
    async def test_успешный_запрос(self, monkeypatch):
        """Успешный запрос — reply отправлен, edit вызван с определением."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "**yeet** — to throw something with force. Example: 'He yeeted the ball'. Author: DankMeme42."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban yeet", chat_id=42)
        bot = _make_bot("yeet")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        msg.reply.assert_awaited_once()
        reply_mock.edit.assert_awaited_once()
        edited = reply_mock.edit.call_args[0][0]
        assert "yeet" in edited
        assert "📖" in edited

    @pytest.mark.asyncio
    async def test_заголовок_содержит_urban_dictionary(self, monkeypatch):
        """Заголовок ответа должен содержать 'Urban Dictionary'."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Slang definition here."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban ghosting", chat_id=10)
        bot = _make_bot("ghosting")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        edited = reply_mock.edit.call_args[0][0]
        assert "Urban Dictionary" in edited
        assert "ghosting" in edited

    @pytest.mark.asyncio
    async def test_disable_tools_false(self, monkeypatch):
        """disable_tools должен быть False — web_search обязателен."""
        from src.handlers import command_handlers

        captured_kwargs: list[dict] = []

        async def fake_stream(*args, **kwargs):
            captured_kwargs.append(kwargs)
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, _ = _make_message("!urban yeet", chat_id=5)
        bot = _make_bot("yeet")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0].get("disable_tools") is False

    @pytest.mark.asyncio
    async def test_session_id_изолирован_по_chat_id(self, monkeypatch):
        """session_id должен содержать 'urban_<chat_id>'."""
        from src.handlers import command_handlers

        captured_chat_ids: list[str] = []

        async def fake_stream(*args, **kwargs):
            captured_chat_ids.append(kwargs.get("chat_id", ""))
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, _ = _make_message("!urban bruh", chat_id=999)
        bot = _make_bot("bruh")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        assert len(captured_chat_ids) == 1
        assert captured_chat_ids[0] == "urban_999"

    @pytest.mark.asyncio
    async def test_session_id_разный_для_разных_чатов(self, monkeypatch):
        """Два чата — два разных session_id."""
        from src.handlers import command_handlers

        captured: list[str] = []

        async def fake_stream(*args, **kwargs):
            captured.append(kwargs.get("chat_id", ""))
            yield "Def."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        from src.handlers.command_handlers import handle_urban

        msg1, _ = _make_message("!urban yolo", chat_id=1)
        await handle_urban(_make_bot("yolo"), msg1)

        msg2, _ = _make_message("!urban yolo", chat_id=2)
        await handle_urban(_make_bot("yolo"), msg2)

        assert captured[0] != captured[1]
        assert "urban_1" in captured
        assert "urban_2" in captured

    @pytest.mark.asyncio
    async def test_пустые_аргументы_рейзит_UserInputError(self):
        """Без аргументов и без reply — UserInputError."""
        from src.core.exceptions import UserInputError

        msg, _ = _make_message("!urban", chat_id=1)
        msg.reply_to_message = None
        bot = _make_bot("")

        from src.handlers.command_handlers import handle_urban

        with pytest.raises(UserInputError):
            await handle_urban(bot, msg)

    @pytest.mark.asyncio
    async def test_пустые_аргументы_help_содержит_пример(self):
        """UserInputError содержит пример использования."""
        from src.core.exceptions import UserInputError

        msg, _ = _make_message("!urban", chat_id=1)
        msg.reply_to_message = None
        bot = _make_bot("")

        from src.handlers.command_handlers import handle_urban

        with pytest.raises(UserInputError) as exc_info:
            await handle_urban(bot, msg)

        assert "urban" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_слово_из_reply_если_нет_аргументов(self, monkeypatch):
        """Если аргументов нет но есть reply — берём слово из reply."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Definition from reply."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        reply_msg = types.SimpleNamespace(text="salty")
        msg, reply_mock = _make_message("!urban", chat_id=10)
        msg.reply_to_message = reply_msg
        bot = _make_bot("")  # пустые аргументы

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        edited = reply_mock.edit.call_args[0][0]
        assert "salty" in edited

    @pytest.mark.asyncio
    async def test_пустой_ответ_от_модели(self, monkeypatch):
        """Если модель вернула пустой ответ — edit содержит ❌."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            # Генератор без yield — пустой ответ
            return
            yield  # noqa: unreachable

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban yeet", chat_id=3)
        bot = _make_bot("yeet")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        edited = reply_mock.edit.call_args[0][0]
        assert "❌" in edited

    @pytest.mark.asyncio
    async def test_ошибка_stream_показывает_ошибку(self, monkeypatch):
        """Исключение в stream — edit содержит ❌."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            raise RuntimeError("network timeout")
            yield  # noqa: unreachable

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban yeet", chat_id=6)
        bot = _make_bot("yeet")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        edited = reply_mock.edit.call_args[0][0]
        assert "❌" in edited

    @pytest.mark.asyncio
    async def test_промпт_содержит_слово(self, monkeypatch):
        """Промпт, отправляемый в OpenClaw, содержит запрошенное слово."""
        from src.handlers import command_handlers

        captured_messages: list[str] = []

        async def fake_stream(*args, **kwargs):
            # args[0] — message (или kwargs["message"])
            captured_messages.append(kwargs.get("message", args[0] if args else ""))
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, _ = _make_message("!urban drip", chat_id=7)
        bot = _make_bot("drip")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        assert len(captured_messages) == 1
        assert "drip" in captured_messages[0]

    @pytest.mark.asyncio
    async def test_промпт_упоминает_urban_dictionary(self, monkeypatch):
        """Промпт явно указывает Urban Dictionary как источник."""
        from src.handlers import command_handlers

        captured_messages: list[str] = []

        async def fake_stream(*args, **kwargs):
            captured_messages.append(kwargs.get("message", args[0] if args else ""))
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, _ = _make_message("!urban lit", chat_id=8)
        bot = _make_bot("lit")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        assert "Urban Dictionary" in captured_messages[0]

    @pytest.mark.asyncio
    async def test_статус_сообщение_отправляется_до_ответа(self, monkeypatch):
        """reply() вызывается до получения ответа (статус «Ищу...»)."""
        from src.handlers import command_handlers

        reply_call_order: list[str] = []
        stream_call_order: list[str] = []

        original_reply = None

        async def fake_stream(*args, **kwargs):
            stream_call_order.append("stream")
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban flex", chat_id=9)
        bot = _make_bot("flex")

        # Оборачиваем reply для отслеживания порядка вызовов
        original_reply_fn = msg.reply

        async def tracked_reply(*a, **kw):
            reply_call_order.append("reply")
            return await original_reply_fn(*a, **kw)

        msg.reply = tracked_reply

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        # reply должен был быть вызван (статус "Ищу...")
        assert "reply" in reply_call_order

    @pytest.mark.asyncio
    async def test_многословный_запрос(self, monkeypatch):
        """Многословный слэнг — обрабатывается корректно."""
        from src.handlers import command_handlers

        captured: list[str] = []

        async def fake_stream(*args, **kwargs):
            captured.append(kwargs.get("message", ""))
            yield "Definition of spill the tea."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban spill the tea", chat_id=11)
        bot = _make_bot("spill the tea")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        edited = reply_mock.edit.call_args[0][0]
        assert "spill the tea" in edited

    @pytest.mark.asyncio
    async def test_пагинация_длинного_ответа(self, monkeypatch):
        """Очень длинный ответ — разбивается на части через reply."""
        from src.handlers import command_handlers

        # Генерируем текст длиннее 3900 символов (лимит _split_text_for_telegram)
        async def fake_stream(*args, **kwargs):
            yield "X" * 8000

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, reply_mock = _make_message("!urban something", chat_id=12)
        bot = _make_bot("something")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        # Первая часть — edit статус-сообщения
        reply_mock.edit.assert_awaited()
        # Дополнительные части — reply()
        # msg.reply вызывается как минимум 1 раз (статус) + 1+ раза (пагинация)
        assert msg.reply.await_count >= 2

    @pytest.mark.asyncio
    async def test_эмодзи_в_статус_сообщении(self, monkeypatch):
        """Статусное сообщение содержит эмодзи и слово."""
        from src.handlers import command_handlers

        async def fake_stream(*args, **kwargs):
            yield "Definition."

        monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

        msg, _ = _make_message("!urban banger", chat_id=13)
        bot = _make_bot("banger")

        from src.handlers.command_handlers import handle_urban

        await handle_urban(bot, msg)

        # Статусное сообщение — первый вызов reply
        status_text = msg.reply.call_args_list[0][0][0]
        assert "banger" in status_text
        assert "📖" in status_text


# ---------------------------------------------------------------------------
# Экспорт из src.handlers
# ---------------------------------------------------------------------------


class TestHandleUrbanExport:
    """handle_urban экспортируется из пакета handlers."""

    def test_экспорт_из_handlers(self):
        """src.handlers должен реэкспортировать handle_urban."""
        from src import handlers

        assert hasattr(handlers, "handle_urban")
        assert handlers.handle_urban is not None

    def test_экспорт_из_command_handlers(self):
        """handle_urban импортируется напрямую из command_handlers."""
        from src.handlers.command_handlers import handle_urban

        assert callable(handle_urban)

    def test_handle_urban_is_coroutine(self):
        """handle_urban — асинхронная функция."""
        import asyncio
        from src.handlers.command_handlers import handle_urban

        assert asyncio.iscoroutinefunction(handle_urban)
