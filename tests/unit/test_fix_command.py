# -*- coding: utf-8 -*-
"""
Юнит-тесты для !fix command handler.

Покрываем:
  - handle_fix: текст из аргументов команды
  - handle_fix: текст из reply-сообщения (без аргументов)
  - handle_fix: нет аргументов и нет reply → UserInputError
  - handle_fix: reply есть, но текст пустой → UserInputError
  - handle_fix: caption используется если text=None
  - session_id изолирован как fix_{chat_id}
  - disable_tools=True
  - max_output_tokens=512
  - пустой ответ AI → сообщение об ошибке
  - несколько streaming-чанков склеиваются
  - exception из openclaw → graceful edit с ❌
  - экспорт из handlers
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_fix


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_message(
    command_args: str = "",
    reply_text: str | None = None,
    reply_caption: str | None = None,
    chat_id: int = 42000,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    if reply_text is not None or reply_caption is not None:
        replied = SimpleNamespace(
            text=reply_text,
            caption=reply_caption,
        )
    else:
        replied = None

    edit_mock = AsyncMock()
    sent_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!fix {command_args}".strip(),
        reply=AsyncMock(return_value=sent_msg),
        reply_to_message=replied,
        chat=SimpleNamespace(id=chat_id),
    )

    bot = SimpleNamespace(_get_command_args=lambda _m: command_args)
    return bot, msg


def _async_gen(*values: str):
    """Создаёт AsyncGenerator из списка строк."""
    async def _gen():
        for v in values:
            yield v
    return _gen()


# ===========================================================================
# Валидация входных данных
# ===========================================================================


class TestHandleFixValidation:
    """Проверка обязательных условий для !fix."""

    @pytest.mark.asyncio
    async def test_нет_аргументов_и_нет_reply_бросает_UserInputError(self) -> None:
        """Ни аргументов, ни reply → UserInputError."""
        bot, msg = _make_message(command_args="", reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_fix(bot, msg)
        err = exc_info.value.user_message.lower()
        assert "fix" in err or "reply" in err or "текст" in err

    @pytest.mark.asyncio
    async def test_reply_есть_но_пустой_текст_бросает_UserInputError(self) -> None:
        """Reply есть, text="", caption=None → UserInputError."""
        bot, msg = _make_message(command_args="", reply_text="")
        with pytest.raises(UserInputError):
            await handle_fix(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_text_и_caption_none_бросает_UserInputError(self) -> None:
        """reply_to_message.text=None и caption=None → UserInputError."""
        bot, msg = _make_message(command_args="")
        msg.reply_to_message = SimpleNamespace(text=None, caption=None)
        with pytest.raises(UserInputError):
            await handle_fix(bot, msg)


# ===========================================================================
# Источник текста: аргументы vs reply
# ===========================================================================


class TestHandleFixTextSource:
    """Проверка откуда берётся текст для исправления."""

    @pytest.mark.asyncio
    async def test_текст_из_аргументов_попадает_в_промпт(self) -> None:
        """Если есть аргументы — берём текст из них."""
        bot, msg = _make_message(command_args="привет как дела")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Привет, как дела?"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert len(captured) == 1
        assert "привет как дела" in captured[0]

    @pytest.mark.asyncio
    async def test_текст_из_reply_если_нет_аргументов(self) -> None:
        """Без аргументов — текст берётся из reply_to_message.text."""
        bot, msg = _make_message(command_args="", reply_text="ошибки в текст")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Ошибки в тексте."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert "ошибки в текст" in captured[0]

    @pytest.mark.asyncio
    async def test_caption_используется_если_нет_text(self) -> None:
        """Если reply.text=None — берём caption."""
        bot, msg = _make_message(command_args="", reply_text=None, reply_caption="подпись к фото")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Подпись к фото."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert "подпись к фото" in captured[0]

    @pytest.mark.asyncio
    async def test_аргументы_приоритетнее_reply(self) -> None:
        """Если есть аргументы — reply игнорируется."""
        bot, msg = _make_message(
            command_args="мой текст",
            reply_text="текст в reply",
        )

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Мой текст."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert "мой текст" in captured[0]
        assert "текст в reply" not in captured[0]


# ===========================================================================
# Промпт
# ===========================================================================


class TestHandleFixPrompt:
    """Проверка промпта для !fix."""

    @pytest.mark.asyncio
    async def test_промпт_содержит_инструкцию_исправить(self) -> None:
        """Промпт должен содержать инструкцию об исправлении."""
        bot, msg = _make_message(command_args="тест")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Тест."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        prompt = captured[0].lower()
        # Промпт должен содержать ключевые слова задания
        assert "исправь" in prompt or "грамматик" in prompt or "орфографи" in prompt

    @pytest.mark.asyncio
    async def test_промпт_содержит_инструкцию_только_текст(self) -> None:
        """Промпт должен требовать вернуть ТОЛЬКО исправленный текст."""
        bot, msg = _make_message(command_args="пример")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Пример."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert "только" in captured[0].lower()


# ===========================================================================
# Session ID и параметры
# ===========================================================================


class TestHandleFixSession:
    """Проверка параметров вызова openclaw_client."""

    @pytest.mark.asyncio
    async def test_session_id_изолирован(self) -> None:
        """chat_id передаётся как 'fix_{chat_id}', а не основной chat_id."""
        bot, msg = _make_message(command_args="текст", chat_id=777)

        captured_chat_id: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured_chat_id.append(chat_id)
            yield "Текст."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert captured_chat_id[0] == "fix_777"

    @pytest.mark.asyncio
    async def test_disable_tools_true(self) -> None:
        """!fix вызывает send_message_stream с disable_tools=True."""
        bot, msg = _make_message(command_args="тест")

        captured_kwargs: list[dict] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured_kwargs.append({"disable_tools": disable_tools})
            yield "Тест."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert captured_kwargs[0]["disable_tools"] is True

    @pytest.mark.asyncio
    async def test_max_output_tokens_512(self) -> None:
        """!fix передаёт max_output_tokens=512."""
        bot, msg = _make_message(command_args="тест")

        captured_kwargs: list[dict] = []

        async def fake_stream(message, chat_id, max_output_tokens=None, **_kw):
            captured_kwargs.append({"max_output_tokens": max_output_tokens})
            yield "Тест."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert captured_kwargs[0]["max_output_tokens"] == 512


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleFixResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_сообщение(self) -> None:
        """Ответ AI → edit() вызывается с исправленным текстом."""
        bot, msg = _make_message(command_args="привет как дела")

        async def fake_stream(message, chat_id, **_kw):
            yield "Привет, как дела?"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0]
        assert "Привет, как дела?" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """Если AI вернул пустую строку → сообщение об ошибке."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        assert "пустой" in sent.edit.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_тоже_ошибка(self) -> None:
        """Whitespace-только ответ → сообщение об ошибке."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, **_kw):
            yield "   \n  "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "пустой" in call_text.lower()

    @pytest.mark.asyncio
    async def test_streaming_несколько_чанков_склеиваются(self) -> None:
        """Несколько streaming-чанков → склеиваются в один ответ."""
        bot, msg = _make_message(command_args="привет как дела")

        async def fake_stream(message, chat_id, **_kw):
            yield "Привет, "
            yield "как "
            yield "дела?"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "Привет, как дела?" in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с ❌."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, **_kw):
            raise RuntimeError("connection lost")
            yield  # делаем генератором

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_статусное_сообщение_отправляется_перед_вызовом_ai(self) -> None:
        """reply() вызывается до вызова send_message_stream."""
        bot, msg = _make_message(command_args="текст")
        call_order: list[str] = []

        original_reply = msg.reply

        async def tracking_reply(text):
            call_order.append("reply")
            return await original_reply(text)

        msg.reply = tracking_reply

        async def fake_stream(message, chat_id, **_kw):
            call_order.append("stream")
            yield "Текст."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_fix(bot, msg)

        assert call_order[0] == "reply", "reply должен вызываться до stream"


# ===========================================================================
# Экспорт из handlers
# ===========================================================================


class TestHandleFixExported:
    """handle_fix должен быть экспортирован из модуля handlers."""

    def test_handle_fix_importable_from_command_handlers(self) -> None:
        """handle_fix импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_fix as hf  # noqa: F401
        assert callable(hf)

    def test_handle_fix_importable_from_handlers_package(self) -> None:
        """handle_fix импортируется из src.handlers."""
        from src.handlers import handle_fix as hf  # noqa: F401
        assert callable(hf)

    def test_handle_fix_in_all(self) -> None:
        """handle_fix присутствует в __all__ пакета handlers."""
        import src.handlers as handlers_pkg
        assert "handle_fix" in handlers_pkg.__all__
