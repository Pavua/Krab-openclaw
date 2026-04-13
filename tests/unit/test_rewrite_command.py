# -*- coding: utf-8 -*-
"""
Юнит-тесты для !rewrite command handler.

Покрываем:
  - handle_rewrite: режимы formal / casual / short / дефолт
  - получение текста из аргументов и из reply
  - отсутствие reply и текста → UserInputError
  - пустой ответ AI → сообщение об ошибке
  - длинный ответ разбивается на части
  - exception из openclaw → graceful error
  - изолированная сессия rewrite_{chat_id}
  - disable_tools=True
  - экспорт из handlers
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_rewrite


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_reply_msg(text: str | None = "Исходный текст для переписки") -> SimpleNamespace:
    """Stub reply_to_message."""
    return SimpleNamespace(text=text, caption=None)


def _make_message(
    command_args: str = "",
    reply_text: str | None = None,
    chat_id: int = 42000,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    if reply_text is not None:
        replied = SimpleNamespace(text=reply_text, caption=None)
    else:
        replied = None

    edit_mock = AsyncMock()
    sent_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!rewrite {command_args}".strip(),
        reply=AsyncMock(return_value=sent_msg),
        reply_to_message=replied,
        chat=SimpleNamespace(id=chat_id),
    )

    bot = SimpleNamespace(_get_command_args=lambda _m: command_args)
    return bot, msg


def _async_stream(*values: str):
    """Создаёт AsyncGenerator из строк."""
    async def _gen():
        for v in values:
            yield v
    return _gen()


# ===========================================================================
# Валидация входных данных
# ===========================================================================


class TestHandleRewriteValidation:
    """Проверка обязательных условий для !rewrite."""

    @pytest.mark.asyncio
    async def test_без_текста_и_reply_бросает_UserInputError(self) -> None:
        """Нет аргументов, нет reply → UserInputError."""
        bot, msg = _make_message(command_args="", reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_rewrite(bot, msg)
        assert "rewrite" in exc_info.value.user_message.lower() or "использование" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_только_режим_без_текста_и_без_reply_бросает_UserInputError(self) -> None:
        """Указан только режим (formal), текст и reply отсутствуют → UserInputError."""
        bot, msg = _make_message(command_args="formal", reply_text=None)
        with pytest.raises(UserInputError):
            await handle_rewrite(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_с_пустым_текстом_бросает_UserInputError(self) -> None:
        """reply_to_message есть, но text='' и caption=None → UserInputError."""
        bot, msg = _make_message(command_args="", reply_text="")
        with pytest.raises(UserInputError):
            await handle_rewrite(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_text_none_caption_none_бросает_UserInputError(self) -> None:
        """reply_to_message.text=None и caption=None → UserInputError."""
        bot, msg = _make_message(command_args="")
        msg.reply_to_message = SimpleNamespace(text=None, caption=None)
        with pytest.raises(UserInputError):
            await handle_rewrite(bot, msg)


# ===========================================================================
# Определение режима
# ===========================================================================


class TestHandleRewriteModeDetection:
    """Корректное определение режима из первого слова."""

    @pytest.mark.asyncio
    async def test_режим_formal_передаётся_в_промпт(self) -> None:
        """!rewrite formal <текст> → промпт содержит инструкцию formal."""
        bot, msg = _make_message(command_args="formal Привет мир")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Добрый день"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert len(captured) == 1
        # Инструкция formal должна быть в промпте
        assert "формальн" in captured[0].lower() or "официальн" in captured[0].lower()
        # Текст без слова-режима
        assert "Привет мир" in captured[0]

    @pytest.mark.asyncio
    async def test_режим_casual_передаётся_в_промпт(self) -> None:
        """!rewrite casual <текст> → промпт содержит инструкцию casual."""
        bot, msg = _make_message(command_args="casual Уважаемый коллега")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Привет"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "разговорн" in captured[0].lower() or "неформальн" in captured[0].lower()

    @pytest.mark.asyncio
    async def test_режим_short_передаётся_в_промпт(self) -> None:
        """!rewrite short <длинный текст> → промпт содержит инструкцию short."""
        bot, msg = _make_message(command_args="short Очень длинное предложение, которое нужно сократить")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Краткий текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "сократи" in captured[0].lower() or "суть" in captured[0].lower()

    @pytest.mark.asyncio
    async def test_дефолтный_режим_без_ключевого_слова(self) -> None:
        """!rewrite <текст> → дефолтный режим (улучшить / переписать)."""
        bot, msg = _make_message(command_args="Текст для улучшения")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Улучшенный текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        # Дефолтная инструкция — улучшить
        assert "улучши" in captured[0].lower() or "читабельн" in captured[0].lower()
        assert "Текст для улучшения" in captured[0]

    @pytest.mark.asyncio
    async def test_неизвестное_первое_слово_не_является_режимом(self) -> None:
        """!rewrite hello world → 'hello' — не режим, весь текст передаётся в дефолтном режиме."""
        bot, msg = _make_message(command_args="hello world текст")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        # Весь текст должен быть в промпте
        assert "hello world текст" in captured[0]


# ===========================================================================
# Источник текста: аргументы vs reply
# ===========================================================================


class TestHandleRewriteTextSource:
    """Текст берётся из аргументов или из reply."""

    @pytest.mark.asyncio
    async def test_текст_из_аргументов_приоритетнее_reply(self) -> None:
        """Если аргумент содержит текст — reply игнорируется."""
        bot, msg = _make_message(command_args="Текст из аргументов", reply_text="Текст из reply")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "Текст из аргументов" in captured[0]
        assert "Текст из reply" not in captured[0]

    @pytest.mark.asyncio
    async def test_текст_из_reply_если_нет_аргументов(self) -> None:
        """Нет аргументов → берётся текст из reply_to_message."""
        bot, msg = _make_message(command_args="", reply_text="Текст из ответного сообщения")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "Текст из ответного сообщения" in captured[0]

    @pytest.mark.asyncio
    async def test_режим_из_аргументов_с_текстом_из_reply(self) -> None:
        """!rewrite formal (в reply) → режим formal, текст из reply."""
        bot, msg = _make_message(command_args="formal", reply_text="Привет, как дела?")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Добрый день, как вы поживаете?"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "формальн" in captured[0].lower() or "официальн" in captured[0].lower()
        assert "Привет, как дела?" in captured[0]

    @pytest.mark.asyncio
    async def test_caption_из_reply_используется_если_нет_text(self) -> None:
        """reply_to_message.text=None, но caption есть → caption используется."""
        bot, msg = _make_message(command_args="")
        msg.reply_to_message = SimpleNamespace(text=None, caption="Подпись к медиа")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "Подпись к медиа" in captured[0]


# ===========================================================================
# Изолированная сессия и disable_tools
# ===========================================================================


class TestHandleRewriteSession:
    """Проверка сессии и параметров вызова openclaw."""

    @pytest.mark.asyncio
    async def test_session_id_rewrite_prefix(self) -> None:
        """chat_id передаётся как 'rewrite_{chat_id}'."""
        bot, msg = _make_message(command_args="тест текст", chat_id=55555)

        captured_chat_id: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured_chat_id.append(chat_id)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert captured_chat_id[0] == "rewrite_55555"

    @pytest.mark.asyncio
    async def test_disable_tools_true(self) -> None:
        """!rewrite вызывает send_message_stream с disable_tools=True."""
        bot, msg = _make_message(command_args="текст для теста")

        captured_kwargs: list[dict] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools=False, **_kw):
            captured_kwargs.append({"disable_tools": disable_tools})
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert captured_kwargs[0]["disable_tools"] is True

    @pytest.mark.asyncio
    async def test_system_prompt_задан(self) -> None:
        """system_prompt передаётся в send_message_stream."""
        bot, msg = _make_message(command_args="текст")

        captured_sp: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools=False, **_kw):
            captured_sp.append(system_prompt)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert len(captured_sp) == 1
        assert captured_sp[0]  # не пустой


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleRewriteResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_сообщение(self) -> None:
        """Ответ AI → edit() вызывается с переписанным текстом."""
        bot, msg = _make_message(command_args="Плохой текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "Хороший текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0]
        assert "Хороший текст" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """AI вернул пустую строку → edit() с сообщением об ошибке."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        assert "пустой" in sent.edit.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_только_пробелы_тоже_пустой_ответ(self) -> None:
        """Whitespace-только ответ → сообщение об ошибке."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "   \n  "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        sent = msg.reply.return_value
        assert "пустой" in sent.edit.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_streaming_несколько_чанков_склеиваются(self) -> None:
        """Несколько streaming-чанков → склеиваются в один результат."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            yield "Часть "
            yield "первая, "
            yield "часть вторая."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "Часть первая, часть вторая." in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с сообщением об ошибке."""
        bot, msg = _make_message(command_args="текст")

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            raise RuntimeError("network error")
            yield  # делаем генератором

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_статус_сообщение_отправляется_перед_запросом(self) -> None:
        """reply() вызывается сразу (статус '✏️ Переписываю...') до ответа AI."""
        bot, msg = _make_message(command_args="текст")

        call_order: list[str] = []

        orig_reply = msg.reply

        async def tracked_reply(text):
            call_order.append(f"reply:{text}")
            return orig_reply.return_value

        msg.reply = tracked_reply

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            call_order.append("stream_start")
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        # reply должен быть вызван раньше stream_start
        assert call_order[0].startswith("reply:")
        assert "Переписываю" in call_order[0]
        assert call_order[1] == "stream_start"


# ===========================================================================
# Режим short + formal с reply (интеграционные сценарии)
# ===========================================================================


class TestHandleRewriteScenarios:
    """Полные сценарии использования."""

    @pytest.mark.asyncio
    async def test_short_с_reply(self) -> None:
        """!short (в reply на длинный текст) → передаёт текст и инструкцию сократить."""
        bot, msg = _make_message(command_args="short", reply_text="Очень длинный и подробный текст с водой")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Краткий текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "Очень длинный и подробный текст с водой" in captured[0]
        assert "сократи" in captured[0].lower() or "суть" in captured[0].lower()

    @pytest.mark.asyncio
    async def test_casual_с_аргументом(self) -> None:
        """!rewrite casual Уважаемые коллеги → разговорный стиль."""
        bot, msg = _make_message(command_args="casual Уважаемые коллеги, прошу вас")

        captured: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            captured.append(message)
            yield "Эй ребят"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_rewrite(bot, msg)

        assert "Уважаемые коллеги, прошу вас" in captured[0]
        assert "разговорн" in captured[0].lower() or "неформальн" in captured[0].lower()

    @pytest.mark.asyncio
    async def test_разные_chat_id_дают_разные_сессии(self) -> None:
        """Два разных chat_id → два разных session_id."""
        results: list[str] = []

        async def fake_stream(message, chat_id, system_prompt, disable_tools, **_kw):
            results.append(chat_id)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            bot1, msg1 = _make_message(command_args="текст", chat_id=111)
            await handle_rewrite(bot1, msg1)

            bot2, msg2 = _make_message(command_args="текст", chat_id=222)
            await handle_rewrite(bot2, msg2)

        assert results[0] == "rewrite_111"
        assert results[1] == "rewrite_222"
        assert results[0] != results[1]


# ===========================================================================
# Экспорт из handlers
# ===========================================================================


class TestHandleRewriteExport:
    """handle_rewrite должен быть экспортирован из модуля handlers."""

    def test_handle_rewrite_importable_from_command_handlers(self) -> None:
        """handle_rewrite импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_rewrite as hr  # noqa: F401
        assert callable(hr)

    def test_handle_rewrite_importable_from_handlers_package(self) -> None:
        """handle_rewrite экспортируется из src.handlers."""
        from src.handlers import handle_rewrite as hr  # noqa: F401
        assert callable(hr)

    def test_handle_rewrite_in_all(self) -> None:
        """handle_rewrite присутствует в __all__ пакета handlers."""
        import src.handlers as handlers_pkg
        assert "handle_rewrite" in handlers_pkg.__all__

    def test_rewrite_modes_dict_complete(self) -> None:
        """_REWRITE_MODES содержит все ожидаемые режимы включая дефолтный."""
        from src.handlers.command_handlers import _REWRITE_MODES
        assert "formal" in _REWRITE_MODES
        assert "casual" in _REWRITE_MODES
        assert "short" in _REWRITE_MODES
        assert "" in _REWRITE_MODES  # дефолтный режим
