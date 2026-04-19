# -*- coding: utf-8 -*-
"""
Тесты для !summary / !catchup — суммаризация истории чата через LLM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _SUMMARY_DEFAULT_N,
    _SUMMARY_MAX_HISTORY_CHARS,
    _SUMMARY_MAX_N,
    _format_chat_history_for_llm,
    handle_catchup,
    handle_summary,
)

# ─────────────────────────────────────────────────────────────────────────────
# Хелперы
# ─────────────────────────────────────────────────────────────────────────────


def _make_pyrogram_msg(
    text: str = "Привет",
    username: str | None = "user1",
    first_name: str | None = "Вася",
    last_name: str | None = None,
    user_id: int = 100,
    hour: int = 12,
    minute: int = 0,
    has_photo: bool = False,
    has_video: bool = False,
    has_voice: bool = False,
    has_sticker: bool = False,
    has_document: bool = False,
) -> MagicMock:
    """Создаёт mock pyrogram Message."""
    msg = MagicMock()
    msg.text = (
        text if not (has_photo or has_video or has_voice or has_sticker or has_document) else None
    )
    msg.caption = None
    msg.photo = MagicMock() if has_photo else None
    msg.video = MagicMock() if has_video else None
    msg.voice = MagicMock() if has_voice else None
    msg.audio = None
    msg.document = MagicMock() if has_document else None
    msg.sticker = MagicMock() if has_sticker else None
    msg.date = datetime(2024, 1, 15, hour, minute, tzinfo=timezone.utc)

    # from_user
    msg.from_user = SimpleNamespace(
        id=user_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    msg.sender_chat = None
    return msg


def _make_bot(chat_id: int = 12345) -> MagicMock:
    """Создаёт mock KraabUserbot."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    bot.client = MagicMock()
    return bot


def _make_message(text: str = "!summary", chat_id: int = 12345) -> MagicMock:
    """Создаёт mock Telegram Message."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=100, username="owner")
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return msg


def _make_async_gen(items: list):
    """Создаёт async generator из списка."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


# ─────────────────────────────────────────────────────────────────────────────
# _format_chat_history_for_llm
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatChatHistoryForLlm:
    def test_basic_text_message(self) -> None:
        """Базовое текстовое сообщение форматируется корректно."""
        msg = _make_pyrogram_msg(text="Привет мир", first_name="Вася", hour=14, minute=30)
        result = _format_chat_history_for_llm([msg])
        assert "[14:30] Вася: Привет мир" == result

    def test_first_and_last_name(self) -> None:
        """Имя и фамилия объединяются через пробел."""
        msg = _make_pyrogram_msg(text="Тест", first_name="Иван", last_name="Иванов")
        result = _format_chat_history_for_llm([msg])
        assert "Иван Иванов: Тест" in result

    def test_username_fallback(self) -> None:
        """При отсутствии имени используется @username."""
        msg = _make_pyrogram_msg(text="Тест", first_name=None, last_name=None, username="johndoe")
        result = _format_chat_history_for_llm([msg])
        assert "@johndoe: Тест" in result

    def test_user_id_fallback(self) -> None:
        """При отсутствии имени и username используется id."""
        msg = _make_pyrogram_msg(
            text="Тест", first_name=None, last_name=None, username=None, user_id=42
        )
        result = _format_chat_history_for_llm([msg])
        assert "42: Тест" in result

    def test_no_from_user_unknown(self) -> None:
        """Сообщение без from_user — sender Unknown."""
        msg = _make_pyrogram_msg(text="Привет")
        msg.from_user = None
        msg.sender_chat = None
        result = _format_chat_history_for_llm([msg])
        assert "Unknown: Привет" in result

    def test_sender_chat(self) -> None:
        """sender_chat.title используется как имя."""
        msg = _make_pyrogram_msg(text="Анонс")
        msg.from_user = None
        msg.sender_chat = SimpleNamespace(title="Мой канал", id=999)
        result = _format_chat_history_for_llm([msg])
        assert "Мой канал: Анонс" in result

    def test_photo_placeholder(self) -> None:
        """Фото без подписи — [фото]."""
        msg = _make_pyrogram_msg(has_photo=True)
        result = _format_chat_history_for_llm([msg])
        assert "[фото]" in result

    def test_video_placeholder(self) -> None:
        """Видео без подписи — [видео]."""
        msg = _make_pyrogram_msg(has_video=True)
        result = _format_chat_history_for_llm([msg])
        assert "[видео]" in result

    def test_voice_placeholder(self) -> None:
        """Голосовое — [голосовое/аудио]."""
        msg = _make_pyrogram_msg(has_voice=True)
        result = _format_chat_history_for_llm([msg])
        assert "[голосовое/аудио]" in result

    def test_sticker_placeholder(self) -> None:
        """Стикер — [стикер]."""
        msg = _make_pyrogram_msg(has_sticker=True)
        result = _format_chat_history_for_llm([msg])
        assert "[стикер]" in result

    def test_document_placeholder(self) -> None:
        """Документ — [документ]."""
        msg = _make_pyrogram_msg(has_document=True)
        result = _format_chat_history_for_llm([msg])
        assert "[документ]" in result

    def test_chronological_order(self) -> None:
        """История разворачивается в хронологический порядок."""
        msg1 = _make_pyrogram_msg(text="Первое", hour=10)
        msg2 = _make_pyrogram_msg(text="Второе", hour=11)
        # get_chat_history даёт новые-первые: msg2, msg1
        result = _format_chat_history_for_llm([msg2, msg1])
        lines = result.split("\n")
        assert "Первое" in lines[0]
        assert "Второе" in lines[1]

    def test_multiple_messages(self) -> None:
        """Несколько сообщений — каждое на своей строке."""
        msgs = [_make_pyrogram_msg(text=f"msg{i}") for i in range(5)]
        result = _format_chat_history_for_llm(msgs)
        assert result.count("\n") == 4

    def test_empty_list(self) -> None:
        """Пустой список — пустая строка."""
        result = _format_chat_history_for_llm([])
        assert result == ""

    def test_no_date(self) -> None:
        """Сообщение без даты — пустой timestamp [  ]."""
        msg = _make_pyrogram_msg(text="без даты")
        msg.date = None
        result = _format_chat_history_for_llm([msg])
        assert result.startswith("[]") and "без даты" in result

    def test_caption_used_when_no_text(self) -> None:
        """caption используется если text пустой."""
        msg = _make_pyrogram_msg(text="Фото с подписью")
        msg.text = None
        msg.caption = "Фото с подписью"
        result = _format_chat_history_for_llm([msg])
        assert "Фото с подписью" in result


# ─────────────────────────────────────────────────────────────────────────────
# handle_summary — парсинг аргументов
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleSummaryArgParsing:
    def _mock_stream(self, text: str = "Краткая сводка"):
        async def _gen():
            yield text

        return _gen()

    def _setup(self, args: str = "", chat_id: int = 12345):
        bot = _make_bot(chat_id)
        bot._get_command_args = MagicMock(return_value=args)
        msg = _make_message(chat_id=chat_id)

        # Мокируем get_chat_history
        pyrogram_msgs = [_make_pyrogram_msg(text=f"msg{i}") for i in range(5)]
        bot.client.get_chat_history = MagicMock(return_value=_make_async_gen(pyrogram_msgs))

        return bot, msg

    @pytest.mark.asyncio
    async def test_no_args_uses_default_n(self) -> None:
        """Без аргументов используется _SUMMARY_DEFAULT_N."""
        bot, msg = self._setup(args="")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        # Проверяем, что get_chat_history вызвался с limit=_SUMMARY_DEFAULT_N
        bot.client.get_chat_history.assert_called_once_with(12345, limit=_SUMMARY_DEFAULT_N)

    @pytest.mark.asyncio
    async def test_numeric_arg_sets_n(self) -> None:
        """Числовой аргумент задаёт N."""
        bot, msg = self._setup(args="30")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        bot.client.get_chat_history.assert_called_once_with(12345, limit=30)

    @pytest.mark.asyncio
    async def test_n_clamped_to_max(self) -> None:
        """N зажимается до _SUMMARY_MAX_N."""
        bot, msg = self._setup(args="9999")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        bot.client.get_chat_history.assert_called_once_with(12345, limit=_SUMMARY_MAX_N)

    @pytest.mark.asyncio
    async def test_n_minimum_1(self) -> None:
        """N не меньше 1."""
        bot, msg = self._setup(args="0")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        bot.client.get_chat_history.assert_called_once_with(12345, limit=1)

    @pytest.mark.asyncio
    async def test_chat_id_arg(self) -> None:
        """Аргумент chat_id задаёт другой чат."""
        bot, msg = self._setup(args="-1001234567890")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        bot.client.get_chat_history.assert_called_once_with(
            -1001234567890, limit=_SUMMARY_DEFAULT_N
        )

    @pytest.mark.asyncio
    async def test_chat_id_and_n(self) -> None:
        """Аргументы chat_id + N работают вместе."""
        bot, msg = self._setup(args="-1001234567890 75")
        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)
        bot.client.get_chat_history.assert_called_once_with(-1001234567890, limit=75)

    @pytest.mark.asyncio
    async def test_invalid_text_arg_raises(self) -> None:
        """Невалидный текстовый аргумент вызывает UserInputError."""
        bot, msg = self._setup(args="invalid_arg")
        with pytest.raises(UserInputError):
            await handle_summary(bot, msg)

    @pytest.mark.asyncio
    async def test_invalid_chat_id_raises(self) -> None:
        """Невалидный chat_id (начинается с '-100' но нечисловой) — ошибка."""
        bot, msg = self._setup(args="-100abc")
        with pytest.raises(UserInputError):
            await handle_summary(bot, msg)


# ─────────────────────────────────────────────────────────────────────────────
# handle_summary — основная логика
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleSummaryMain:
    def _setup(self, pyrogram_msgs=None, args: str = ""):
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value=args)
        msg = _make_message()

        if pyrogram_msgs is None:
            pyrogram_msgs = [_make_pyrogram_msg(text=f"msg{i}") for i in range(5)]

        bot.client.get_chat_history = MagicMock(return_value=_make_async_gen(pyrogram_msgs))
        return bot, msg

    def _mock_stream(self, chunks: list[str] | None = None):
        if chunks is None:
            chunks = ["Краткая сводка чата."]

        async def _gen():
            for c in chunks:
                yield c

        return _gen()

    @pytest.mark.asyncio
    async def test_result_has_header(self) -> None:
        """Результат содержит заголовок."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)

        # Финальный edit должен содержать заголовок
        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "📋" in text
        assert "Сводка чата" in text
        assert "─────────────" in text

    @pytest.mark.asyncio
    async def test_result_contains_llm_output(self) -> None:
        """Результат содержит вывод LLM."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(
                return_value=self._mock_stream(["Обсуждали ", "дедлайны проекта."])
            )
            await handle_summary(bot, msg)

        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "Обсуждали дедлайны проекта." in text

    @pytest.mark.asyncio
    async def test_empty_history(self) -> None:
        """Пустая история — сообщение об отсутствии."""
        bot, msg = self._setup(pyrogram_msgs=[])
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        await handle_summary(bot, msg)

        # Нет вызова stream, статус с пустой историей
        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "пуста" in text or "недоступна" in text

    @pytest.mark.asyncio
    async def test_get_history_exception(self) -> None:
        """Исключение при get_chat_history — ошибка в ответе."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        async def _bad_gen():
            raise RuntimeError("Network error")
            yield  # делает генератором

        bot.client.get_chat_history = MagicMock(return_value=_bad_gen())
        await handle_summary(bot, msg)

        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_llm_exception(self) -> None:
        """Исключение в LLM stream — ошибка в ответе."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        async def _bad_stream():
            raise RuntimeError("LLM timeout")
            yield  # noqa: unreachable

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=_bad_stream())
            await handle_summary(bot, msg)

        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_stream_uses_isolated_session(self) -> None:
        """LLM вызывается с изолированной сессией (не портит основной контекст)."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)
        chat_id_used = []

        async def _capture_stream(**kwargs):
            chat_id_used.append(kwargs.get("chat_id", ""))
            yield "Ок"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_capture_stream)
            await handle_summary(bot, msg)

        assert len(chat_id_used) == 1
        assert chat_id_used[0].startswith("summary_")

    @pytest.mark.asyncio
    async def test_stream_disable_tools(self) -> None:
        """LLM вызывается с disable_tools=True."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)
        kwargs_captured = {}

        async def _capture_stream(**kwargs):
            kwargs_captured.update(kwargs)
            yield "Ок"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_capture_stream)
            await handle_summary(bot, msg)

        assert kwargs_captured.get("disable_tools") is True

    @pytest.mark.asyncio
    async def test_long_result_truncated(self) -> None:
        """Результат длиннее 4096 символов обрезается."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        long_chunk = "A" * 5000

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream([long_chunk]))
            await handle_summary(bot, msg)

        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert len(text) <= 4096

    @pytest.mark.asyncio
    async def test_history_truncated_at_max_chars(self) -> None:
        """Длинная история обрезается до _SUMMARY_MAX_HISTORY_CHARS."""
        # Создаём много сообщений с длинным текстом
        big_msgs = [_make_pyrogram_msg(text="X" * 1000) for _ in range(50)]
        bot, msg = self._setup(pyrogram_msgs=big_msgs)
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)
        prompt_captured = []

        async def _capture_stream(**kwargs):
            prompt_captured.append(kwargs.get("message", ""))
            yield "Ок"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_capture_stream)
            await handle_summary(bot, msg)

        assert len(prompt_captured) == 1
        # История в промпте не должна превышать лимит + некоторый overhead промпта
        assert len(prompt_captured[0]) < _SUMMARY_MAX_HISTORY_CHARS + 500

    @pytest.mark.asyncio
    async def test_status_message_sent_first(self) -> None:
        """Первый reply — плейсхолдер статуса."""
        bot, msg = self._setup()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)

        first_reply = msg.reply.call_args_list[0][0][0]
        assert "Собираю" in first_reply

    @pytest.mark.asyncio
    async def test_edit_fallback_to_reply(self) -> None:
        """Если финальный edit падает — отправляется новый reply."""
        bot, msg = self._setup()
        status_mock = MagicMock()
        # Первый вызов edit (⏳ Генерирую...) успешен, финальный — падает
        call_count = 0

        async def _edit_side_effect(text):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise Exception("Flood wait")

        status_mock.edit = AsyncMock(side_effect=_edit_side_effect)
        msg.reply = AsyncMock(return_value=status_mock)

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(return_value=self._mock_stream())
            await handle_summary(bot, msg)

        # После неудачного финального edit должен быть ещё один reply
        assert msg.reply.call_count >= 2


# ─────────────────────────────────────────────────────────────────────────────
# handle_catchup
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleCatchup:
    @pytest.mark.asyncio
    async def test_catchup_uses_100_messages(self) -> None:
        """!catchup запрашивает ровно 100 сообщений."""
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="")
        msg = _make_message()

        pyrogram_msgs = [_make_pyrogram_msg(text=f"msg{i}") for i in range(10)]
        bot.client.get_chat_history = MagicMock(return_value=_make_async_gen(pyrogram_msgs))

        async def _stream(**kwargs):
            yield "Сводка"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_stream)
            await handle_catchup(bot, msg)

        bot.client.get_chat_history.assert_called_once_with(12345, limit=100)

    @pytest.mark.asyncio
    async def test_catchup_restores_get_args(self) -> None:
        """После catchup оригинальный _get_command_args восстановлен."""
        bot = _make_bot()
        original_fn = MagicMock(return_value="some_args")
        bot._get_command_args = original_fn
        msg = _make_message()

        pyrogram_msgs = [_make_pyrogram_msg(text="msg")]
        bot.client.get_chat_history = MagicMock(return_value=_make_async_gen(pyrogram_msgs))

        async def _stream(**kwargs):
            yield "ok"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_stream)
            await handle_catchup(bot, msg)

        assert bot._get_command_args is original_fn

    @pytest.mark.asyncio
    async def test_catchup_restores_on_exception(self) -> None:
        """_get_command_args восстанавливается даже при исключении."""
        bot = _make_bot()
        original_fn = MagicMock(return_value="")
        bot._get_command_args = original_fn
        msg = _make_message()

        # get_chat_history кидает исключение
        async def _bad_gen():
            raise RuntimeError("oops")
            yield

        bot.client.get_chat_history = MagicMock(return_value=_bad_gen())
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        await handle_catchup(bot, msg)

        assert bot._get_command_args is original_fn

    @pytest.mark.asyncio
    async def test_catchup_result_format(self) -> None:
        """Результат catchup имеет правильный формат."""
        bot = _make_bot()
        bot._get_command_args = MagicMock(return_value="")
        msg = _make_message()
        status_mock = MagicMock(edit=AsyncMock())
        msg.reply = AsyncMock(return_value=status_mock)

        pyrogram_msgs = [_make_pyrogram_msg(text=f"msg{i}") for i in range(5)]
        bot.client.get_chat_history = MagicMock(return_value=_make_async_gen(pyrogram_msgs))

        async def _stream(**kwargs):
            yield "Важные события"

        with patch("src.handlers.command_handlers.openclaw_client") as mock_oc:
            mock_oc.send_message_stream = MagicMock(side_effect=_stream)
            await handle_catchup(bot, msg)

        final_call = status_mock.edit.call_args_list[-1]
        text = final_call[0][0]
        assert "📋" in text
        assert "Важные события" in text


# ─────────────────────────────────────────────────────────────────────────────
# Константы
# ─────────────────────────────────────────────────────────────────────────────


class TestConstants:
    def test_default_n_is_50(self) -> None:
        assert _SUMMARY_DEFAULT_N == 50

    def test_max_n_is_500(self) -> None:
        assert _SUMMARY_MAX_N == 500

    def test_max_history_chars_reasonable(self) -> None:
        """Лимит истории > 10к (чтобы вмещать реальный контекст)."""
        assert _SUMMARY_MAX_HISTORY_CHARS >= 10_000
