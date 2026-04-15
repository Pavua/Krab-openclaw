# -*- coding: utf-8 -*-
"""
Тесты обработчика !explain — объяснение кода через AI.

Покрытие:
  1.  Пустой запрос без reply → UserInputError с подсказкой
  2.  Пустой запрос + reply с кодом → берёт текст из reply
  3.  Пустой reply (нет текста) + нет аргументов → UserInputError
  4.  Нет reply_to_message attr + нет аргументов → UserInputError
  5.  Прямой код в аргументах → отправляет объяснение
  6.  Пустой ответ AI → сообщение об ошибке
  7.  Исключение от openclaw → редактирует сообщение с ошибкой
  8.  Сессия изолирована: session_id содержит chat_id
  9.  disable_tools=True передаётся в send_message_stream
  10. max_output_tokens=1024 передаётся в send_message_stream
  11. Промпт содержит исходный код
  12. Промпт содержит ключевые слова из _EXPLAIN_PROMPT
  13. Пагинация при длинном ответе
  14. Reply с кодом: caption используется если нет text
  15. Заголовок ответа содержит «Объяснение кода»
  16. Индикаторы «часть N/M» при пагинации на 3+ части
  17. Нет суффикса во второй части при 2 частях
  18. Код из reply приоритетнее пустых аргументов
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as ch_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_explain, _EXPLAIN_PROMPT

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message(chat_id: int = 42000, reply_text: str | None = None) -> tuple:
    """
    Мок Telegram-сообщения.

    Возвращает (msg, sent), где sent — объект, возвращаемый reply().
    """
    sent = SimpleNamespace(edit=AsyncMock())
    reply_msg = None
    if reply_text is not None:
        reply_msg = SimpleNamespace(
            text=reply_text,
            caption=None,
        )
    msg = SimpleNamespace(
        reply=AsyncMock(return_value=sent),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=reply_msg,
    )
    return msg, sent


def _make_message_caption_reply(chat_id: int = 42000, caption: str = "") -> tuple:
    """Мок с reply, у которого нет text, но есть caption."""
    sent = SimpleNamespace(edit=AsyncMock())
    reply_msg = SimpleNamespace(text=None, caption=caption)
    msg = SimpleNamespace(
        reply=AsyncMock(return_value=sent),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=reply_msg,
    )
    return msg, sent


def _make_message_no_reply_attr(chat_id: int = 42000) -> tuple:
    """Мок без атрибута reply_to_message (как будто его нет вообще)."""
    sent = SimpleNamespace(edit=AsyncMock())
    msg = SimpleNamespace(
        reply=AsyncMock(return_value=sent),
        chat=SimpleNamespace(id=chat_id),
        # reply_to_message не определён намеренно
    )
    return msg, sent


def _make_async_gen(items: list[str]):
    """Создаёт async-генератор из списка строк."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


# ---------------------------------------------------------------------------
# 1. Пустой запрос без reply → UserInputError
# ---------------------------------------------------------------------------

class TestHandleExplainEmptyQuery:
    """Пустые входные данные → UserInputError."""

    @pytest.mark.asyncio
    async def test_пустой_запрос_без_reply_бросает_userinputerror(self):
        bot = _make_bot("")
        msg, _ = _make_message(reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_explain(bot, msg)
        assert "!explain" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_userinputerror_подсказка_содержит_reply_инструкцию(self):
        bot = _make_bot("")
        msg, _ = _make_message(reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_explain(bot, msg)
        assert "reply" in exc_info.value.user_message.lower() or "ответь" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# 2. Пустые аргументы + reply с кодом → берёт текст из reply
# ---------------------------------------------------------------------------

class TestHandleExplainFromReply:
    """Код берётся из reply-сообщения."""

    @pytest.mark.asyncio
    async def test_reply_с_кодом_вызывает_openclaw(self):
        bot = _make_bot("")
        msg, sent = _make_message(reply_text="print('hello')")

        mock_stream = _make_async_gen(["Этот код выводит строку 'hello'."])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ) as mock_send:
            await handle_explain(bot, msg)

        mock_send.assert_called_once()
        sent.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_reply_код_присутствует_в_промпте(self):
        bot = _make_bot("")
        code = "def foo(): return 42"
        msg, sent = _make_message(reply_text=code)

        captured_prompt: list[str] = []

        async def _fake_stream(message, **kwargs):
            captured_prompt.append(message)
            yield "объяснение"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert code in captured_prompt[0]


# ---------------------------------------------------------------------------
# 3. Пустой reply (нет текста и caption) + нет аргументов → UserInputError
# ---------------------------------------------------------------------------

class TestHandleExplainEmptyReply:

    @pytest.mark.asyncio
    async def test_reply_с_пустым_текстом_бросает_userinputerror(self):
        bot = _make_bot("")
        msg, _ = _make_message(reply_text="")
        with pytest.raises(UserInputError):
            await handle_explain(bot, msg)


# ---------------------------------------------------------------------------
# 4. Нет атрибута reply_to_message + нет аргументов → UserInputError
# ---------------------------------------------------------------------------

class TestHandleExplainNoReplyAttr:

    @pytest.mark.asyncio
    async def test_без_атрибута_reply_бросает_userinputerror(self):
        bot = _make_bot("")
        msg, _ = _make_message_no_reply_attr()
        with pytest.raises(UserInputError):
            await handle_explain(bot, msg)


# ---------------------------------------------------------------------------
# 5. Прямой код в аргументах → вызывает openclaw, редактирует msg
# ---------------------------------------------------------------------------

class TestHandleExplainDirectCode:

    @pytest.mark.asyncio
    async def test_прямой_код_вызывает_openclaw(self):
        code = "x = 1 + 1"
        bot = _make_bot(code)
        msg, sent = _make_message()

        mock_stream = _make_async_gen(["Складывает два числа."])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ) as mock_send:
            await handle_explain(bot, msg)

        mock_send.assert_called_once()
        sent.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_ответ_редактируется_с_заголовком(self):
        bot = _make_bot("pass")
        msg, sent = _make_message()

        mock_stream = _make_async_gen(["Ничего не делает."])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        call_args = sent.edit.call_args[0][0]
        assert "Объяснение кода" in call_args


# ---------------------------------------------------------------------------
# 6. Пустой ответ AI → сообщение об ошибке
# ---------------------------------------------------------------------------

class TestHandleExplainEmptyAIResponse:

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_редактирует_ошибку(self):
        bot = _make_bot("some code")
        msg, sent = _make_message()

        mock_stream = _make_async_gen([])  # пустой генератор
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        call_args = sent.edit.call_args[0][0]
        assert "❌" in call_args or "не смог" in call_args.lower()

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_даёт_ошибку(self):
        bot = _make_bot("code here")
        msg, sent = _make_message()

        mock_stream = _make_async_gen(["   \n  "])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        call_args = sent.edit.call_args[0][0]
        assert "❌" in call_args


# ---------------------------------------------------------------------------
# 7. Исключение от openclaw → редактирует msg с ошибкой
# ---------------------------------------------------------------------------

class TestHandleExplainException:

    @pytest.mark.asyncio
    async def test_исключение_openclaw_отображается_в_chat(self):
        bot = _make_bot("x = 1")
        msg, sent = _make_message()

        async def _fail(**kwargs):
            raise RuntimeError("модель недоступна")
            yield  # делает функцию генератором

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=RuntimeError("модель недоступна"),
        ):
            await handle_explain(bot, msg)

        call_args = sent.edit.call_args[0][0]
        assert "❌" in call_args

    @pytest.mark.asyncio
    async def test_текст_исключения_содержится_в_ответе(self):
        bot = _make_bot("code")
        msg, sent = _make_message()

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=ValueError("bad value"),
        ):
            await handle_explain(bot, msg)

        call_args = sent.edit.call_args[0][0]
        assert "bad value" in call_args


# ---------------------------------------------------------------------------
# 8. Сессия изолирована: session_id содержит chat_id
# ---------------------------------------------------------------------------

class TestHandleExplainSession:

    @pytest.mark.asyncio
    async def test_session_id_содержит_chat_id(self):
        chat_id = 99999
        bot = _make_bot("code")
        msg, sent = _make_message(chat_id=chat_id)

        captured_kwargs: dict = {}

        async def _fake_stream(message, **kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert str(chat_id) in str(captured_kwargs.get("chat_id", ""))

    @pytest.mark.asyncio
    async def test_session_id_начинается_с_explain(self):
        bot = _make_bot("code")
        msg, sent = _make_message(chat_id=12345)

        captured_kwargs: dict = {}

        async def _fake_stream(message, **kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert str(captured_kwargs.get("chat_id", "")).startswith("explain_")


# ---------------------------------------------------------------------------
# 9. disable_tools=True передаётся
# ---------------------------------------------------------------------------

class TestHandleExplainDisableTools:

    @pytest.mark.asyncio
    async def test_disable_tools_true(self):
        bot = _make_bot("x = 1")
        msg, sent = _make_message()

        captured_kwargs: dict = {}

        async def _fake_stream(message, **kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert captured_kwargs.get("disable_tools") is True


# ---------------------------------------------------------------------------
# 10. max_output_tokens=1024 передаётся
# ---------------------------------------------------------------------------

class TestHandleExplainMaxTokens:

    @pytest.mark.asyncio
    async def test_max_output_tokens_1024(self):
        bot = _make_bot("code")
        msg, sent = _make_message()

        captured_kwargs: dict = {}

        async def _fake_stream(message, **kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert captured_kwargs.get("max_output_tokens") == 1024


# ---------------------------------------------------------------------------
# 11–12. Промпт содержит код и ключевые слова _EXPLAIN_PROMPT
# ---------------------------------------------------------------------------

class TestHandleExplainPrompt:

    @pytest.mark.asyncio
    async def test_промпт_содержит_код(self):
        code = "lambda x: x * 2"
        bot = _make_bot(code)
        msg, _ = _make_message()

        captured_prompt: list[str] = []

        async def _fake_stream(message, **kwargs):
            captured_prompt.append(message)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert code in captured_prompt[0]

    @pytest.mark.asyncio
    async def test_промпт_содержит_ключевые_слова_из_EXPLAIN_PROMPT(self):
        bot = _make_bot("pass")
        msg, _ = _make_message()

        captured_prompt: list[str] = []

        async def _fake_stream(message, **kwargs):
            captured_prompt.append(message)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        # _EXPLAIN_PROMPT должен входить в prompt
        assert _EXPLAIN_PROMPT in captured_prompt[0]


# ---------------------------------------------------------------------------
# 13. Пагинация при длинном ответе
# ---------------------------------------------------------------------------

class TestHandleExplainPagination:

    @pytest.mark.asyncio
    async def test_длинный_ответ_разбивается_на_части(self):
        bot = _make_bot("some code")
        msg, sent = _make_message()

        # Создаём ответ длиннее лимита пагинации (3900 символов)
        long_text = "A" * 4100

        mock_stream = _make_async_gen([long_text])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        # Первое сообщение — edit, последующие — reply
        assert sent.edit.call_count == 1
        assert msg.reply.call_count >= 2  # первый reply для "Анализирую..." + второй для продолжения

    @pytest.mark.asyncio
    async def test_первая_часть_содержит_индикатор_1_из_N(self):
        bot = _make_bot("code")
        msg, sent = _make_message()

        long_text = "B" * 4100

        mock_stream = _make_async_gen([long_text])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        first_edit = sent.edit.call_args[0][0]
        assert "1/" in first_edit


# ---------------------------------------------------------------------------
# 14. Reply с caption (нет text) используется
# ---------------------------------------------------------------------------

class TestHandleExplainCaptionFallback:

    @pytest.mark.asyncio
    async def test_caption_используется_когда_нет_text(self):
        caption_code = "print('caption code')"
        bot = _make_bot("")
        msg, sent = _make_message_caption_reply(caption=caption_code)

        captured_prompt: list[str] = []

        async def _fake_stream(message, **kwargs):
            captured_prompt.append(message)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert caption_code in captured_prompt[0]


# ---------------------------------------------------------------------------
# 15. Заголовок содержит «Объяснение кода»
# ---------------------------------------------------------------------------

class TestHandleExplainHeader:

    @pytest.mark.asyncio
    async def test_заголовок_ответа_содержит_объяснение_кода(self):
        bot = _make_bot("x = 42")
        msg, sent = _make_message()

        mock_stream = _make_async_gen(["Присваивает переменной x значение 42."])
        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        first_edit = sent.edit.call_args[0][0]
        assert "Объяснение кода" in first_edit


# ---------------------------------------------------------------------------
# 16–17. Индикаторы пагинации
# ---------------------------------------------------------------------------

class TestHandleExplainPaginationIndicators:

    @pytest.mark.asyncio
    async def test_три_части_имеют_суффикс_в_третьей(self):
        bot = _make_bot("code")
        msg, sent = _make_message()

        # Текст достаточно длинный для 3+ частей
        very_long = "C" * 9000

        mock_stream = _make_async_gen([very_long])
        replies_texts: list[str] = []

        original_reply = msg.reply

        async def capture_reply(text, **kwargs):
            replies_texts.append(text)
            return sent

        msg.reply = capture_reply

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        # Последний reply-вызов для 3+ частей должен содержать суффикс
        combined = " ".join(replies_texts)
        # При 3+ частях суффикс вида "_(часть N/M)_" должен быть
        assert "часть" in combined

    @pytest.mark.asyncio
    async def test_две_части_нет_суффикса_во_второй(self):
        """При ровно 2 частях вторая часть НЕ должна содержать суффикс части."""
        bot = _make_bot("code")
        msg, sent = _make_message()

        # Нужен текст чуть больше 3900, но меньше 7800 (точно 2 части)
        medium_text = "D" * 4200

        mock_stream = _make_async_gen([medium_text])
        second_reply_text: list[str] = []
        call_count = [0]
        original_sent = SimpleNamespace(edit=AsyncMock())

        async def capture_reply(text, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                # Второй reply — продолжение текста (первый — "Анализирую...")
                second_reply_text.append(text)
            return original_sent

        msg.reply = capture_reply

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            return_value=mock_stream,
        ):
            await handle_explain(bot, msg)

        if second_reply_text:
            # При 2 частях suffix="" (пустая строка), т.е. суффикс "часть 2/2" не добавляется
            assert "часть 2/2" not in second_reply_text[0]


# ---------------------------------------------------------------------------
# 18. Код из reply приоритетнее пустых аргументов
# ---------------------------------------------------------------------------

class TestHandleExplainReplyPriority:

    @pytest.mark.asyncio
    async def test_reply_используется_когда_аргументы_пусты(self):
        reply_code = "return True"
        bot = _make_bot("")  # пустые аргументы
        msg, sent = _make_message(reply_text=reply_code)

        captured_prompt: list[str] = []

        async def _fake_stream(message, **kwargs):
            captured_prompt.append(message)
            yield "ok"

        with patch.object(
            ch_module.openclaw_client,
            "send_message_stream",
            side_effect=_fake_stream,
        ):
            await handle_explain(bot, msg)

        assert reply_code in captured_prompt[0]
