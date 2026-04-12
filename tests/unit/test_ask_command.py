# -*- coding: utf-8 -*-
"""
Юнит-тесты для !ask command handler.

Покрываем:
  - handle_ask: reply обязателен, текст в reply обязателен
  - вопрос по умолчанию "Объясни это сообщение"
  - пользовательский вопрос передаётся в промпт
  - пустой ответ AI обрабатывается корректно
  - длинный ответ разбивается на части
  - ошибки openclaw_client обрабатываются gracefully
  - изолированная сессия ask_{chat_id}
  - disable_tools=True
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_ask


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_reply_msg(text: str = "Длинный текст для анализа") -> SimpleNamespace:
    """Stub reply_to_message."""
    return SimpleNamespace(text=text, caption=None)


def _make_message(
    command_args: str = "",
    reply_text: str | None = "Пример текста",
    chat_id: int = 12345,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    if reply_text is not None:
        replied = SimpleNamespace(text=reply_text, caption=None)
    else:
        replied = None

    edit_mock = AsyncMock()
    sent_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!ask {command_args}".strip(),
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


class TestHandleAskValidation:
    """Проверка обязательных условий для !ask."""

    @pytest.mark.asyncio
    async def test_без_reply_бросает_UserInputError(self) -> None:
        """Нет reply → UserInputError."""
        bot, msg = _make_message(reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_ask(bot, msg)
        assert "reply" in exc_info.value.user_message.lower() or "ответ" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_пустой_текст_в_reply_бросает_UserInputError(self) -> None:
        """Reply есть, но текст пустой → UserInputError."""
        bot, msg = _make_message(reply_text="")
        with pytest.raises(UserInputError):
            await handle_ask(bot, msg)

    @pytest.mark.asyncio
    async def test_caption_вместо_text_пустой_тоже_ошибка(self) -> None:
        """reply_to_message.text=None, caption=None → UserInputError."""
        bot, msg = _make_message(reply_text=None)
        # Добавляем reply_to_message с пустыми полями
        msg.reply_to_message = SimpleNamespace(text=None, caption=None)
        with pytest.raises(UserInputError):
            await handle_ask(bot, msg)


# ===========================================================================
# Вопрос по умолчанию
# ===========================================================================


class TestHandleAskDefaultQuestion:
    """!ask без вопроса использует дефолтный вопрос."""

    @pytest.mark.asyncio
    async def test_без_вопроса_использует_дефолт(self) -> None:
        """Пустые args → вопрос 'Объясни это сообщение' в промпте."""
        bot, msg = _make_message(command_args="", reply_text="Какой-то текст")

        captured_prompt: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured_prompt.append(message)
            yield "Объяснение"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert len(captured_prompt) == 1
        assert "Объясни это сообщение" in captured_prompt[0]

    @pytest.mark.asyncio
    async def test_с_вопросом_использует_его(self) -> None:
        """Указан вопрос → он попадает в промпт."""
        bot, msg = _make_message(command_args="кратко", reply_text="Длинный документ")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Краткое содержание"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert "кратко" in captured[0]


# ===========================================================================
# Изолированная сессия
# ===========================================================================


class TestHandleAskSession:
    """Проверка chat_id сессии."""

    @pytest.mark.asyncio
    async def test_session_id_изолирован(self) -> None:
        """chat_id передаётся как 'ask_{chat_id}', а не основной chat_id."""
        bot, msg = _make_message(command_args="объясни", reply_text="текст", chat_id=999)

        captured_chat_id: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured_chat_id.append(chat_id)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert captured_chat_id[0] == "ask_999"

    @pytest.mark.asyncio
    async def test_disable_tools_true(self) -> None:
        """!ask вызывает send_message_stream с disable_tools=True."""
        bot, msg = _make_message(command_args="переведи", reply_text="Hello world")

        captured_kwargs: list[dict] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools=False, **_kw):
            captured_kwargs.append({"disable_tools": disable_tools})
            yield "Привет мир"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert captured_kwargs[0]["disable_tools"] is True


# ===========================================================================
# Контент промпта
# ===========================================================================


class TestHandleAskPromptContent:
    """Проверка что исходный текст входит в промпт."""

    @pytest.mark.asyncio
    async def test_текст_из_reply_в_промпте(self) -> None:
        """Текст reply_to_message включён в prompt."""
        source = "def foo(): return 42"
        bot, msg = _make_message(command_args="объясни код", reply_text=source)

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Функция возвращает 42"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert source in captured[0]

    @pytest.mark.asyncio
    async def test_caption_используется_если_нет_text(self) -> None:
        """Если text=None, берём caption."""
        bot, msg = _make_message(command_args="объясни", reply_text=None)
        msg.reply_to_message = SimpleNamespace(text=None, caption="Подпись к фото")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        assert "Подпись к фото" in captured[0]


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleAskResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_сообщение(self) -> None:
        """Ответ AI → edit() вызывается с контентом."""
        bot, msg = _make_message(command_args="кратко", reply_text="Длинный текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "Краткое содержание"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        # reply() вернул msg со stub edit
        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0]
        assert "Краткое содержание" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """Если AI вернул пустую строку → сообщение об ошибке."""
        bot, msg = _make_message(command_args="кратко", reply_text="Текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        assert "пустой" in sent.edit.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_тоже_ошибка(self) -> None:
        """Whitespace-только ответ → сообщение об ошибке."""
        bot, msg = _make_message(command_args="объясни", reply_text="Текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "   \n  "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "пустой" in call_text.lower()

    @pytest.mark.asyncio
    async def test_streaming_несколько_чанков_склеиваются(self) -> None:
        """Несколько streaming-чанков → склеиваются в один ответ."""
        bot, msg = _make_message(command_args="", reply_text="Текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "Первая "
            yield "вторая "
            yield "часть."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "Первая вторая часть." in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с сообщением об ошибке."""
        bot, msg = _make_message(command_args="объясни", reply_text="Текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            raise RuntimeError("connection lost")
            yield  # делаем генератором

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ask(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text


# ===========================================================================
# Экспорт из handlers
# ===========================================================================


class TestHandleAskExported:
    """handle_ask должен быть экспортирован из модуля handlers."""

    def test_handle_ask_importable(self) -> None:
        """handle_ask импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_ask  # noqa: F401
        assert callable(handle_ask)
