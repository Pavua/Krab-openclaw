# -*- coding: utf-8 -*-
"""
Тесты для команды !history — статистика чата за последние 1000 сообщений.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_history


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot() -> MagicMock:
    """Mock-бот с client."""
    bot = MagicMock()
    bot.client = MagicMock()
    return bot


def _make_message(chat_id: int = -1001234567890) -> MagicMock:
    """Mock-сообщение с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    return msg


def _make_msg_obj(
    *,
    text: str | None = "hello",
    photo=None,
    video=None,
    video_note=None,
    voice=None,
    audio=None,
    document=None,
    date: datetime.datetime | None = None,
) -> MagicMock:
    """Создать mock-объект сообщения для get_chat_history."""
    m = MagicMock()
    m.text = text
    m.photo = photo
    m.video = video
    m.video_note = video_note
    m.voice = voice
    m.audio = audio
    m.document = document
    if date is None:
        date = datetime.datetime(2024, 6, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)
    m.date = date
    return m


async def _aiter_gen(items):
    for item in items:
        yield item


def _aiter(items):
    """Создать async iterable из списка."""
    return _aiter_gen(items)


# ---------------------------------------------------------------------------
# Тесты: базовый формат вывода
# ---------------------------------------------------------------------------


class TestHistoryOutputFormat:
    """Проверяем правильность формата ответа !history."""

    @pytest.mark.asyncio
    async def test_заголовок_присутствует(self) -> None:
        """Ответ содержит '📈 Chat History Stats'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Chat History Stats" in text

    @pytest.mark.asyncio
    async def test_разделитель_присутствует(self) -> None:
        """Ответ содержит разделитель '─────'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "─────────────" in text

    @pytest.mark.asyncio
    async def test_строка_messages_присутствует(self) -> None:
        """Ответ содержит строку 'Messages:'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Messages:" in text

    @pytest.mark.asyncio
    async def test_строка_most_active_присутствует(self) -> None:
        """Ответ содержит строку 'Most active:'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Most active:" in text

    @pytest.mark.asyncio
    async def test_строка_average_присутствует(self) -> None:
        """Ответ содержит строку 'Average:'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Average:" in text

    @pytest.mark.asyncio
    async def test_строка_first_last_присутствует(self) -> None:
        """Ответ содержит 'First:' и 'Last:'."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "First:" in text
        assert "Last:" in text

    @pytest.mark.asyncio
    async def test_строка_типов_присутствует(self) -> None:
        """Ответ содержит счётчики Text, Photo, Video, Voice, Docs, Other."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Text:" in text
        assert "Photo:" in text
        assert "Video:" in text
        assert "Voice:" in text
        assert "Docs:" in text
        assert "Other:" in text


# ---------------------------------------------------------------------------
# Тесты: подсчёт сообщений
# ---------------------------------------------------------------------------


class TestHistoryMessageCounting:
    """Проверяем точность подсчёта сообщений."""

    @pytest.mark.asyncio
    async def test_total_count_одно_сообщение(self) -> None:
        """Одно сообщение — Messages: 1."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Messages: 1" in text

    @pytest.mark.asyncio
    async def test_total_count_много_сообщений(self) -> None:
        """100 сообщений — Messages: 100."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj() for _ in range(100)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Messages: 100" in text

    @pytest.mark.asyncio
    async def test_текстовые_сообщения_считаются(self) -> None:
        """3 текстовых сообщения — Text: 3."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj(text=f"hello {i}") for i in range(3)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Text: 3" in text

    @pytest.mark.asyncio
    async def test_фото_считаются(self) -> None:
        """2 фото — Photo: 2."""
        bot = _make_bot()
        msg = _make_message()
        ph = MagicMock()
        msgs = [_make_msg_obj(text=None, photo=ph) for _ in range(2)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Photo: 2" in text

    @pytest.mark.asyncio
    async def test_видео_считается(self) -> None:
        """1 видео — Video: 1."""
        bot = _make_bot()
        msg = _make_message()
        vid = MagicMock()
        msgs = [_make_msg_obj(text=None, video=vid)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Video: 1" in text

    @pytest.mark.asyncio
    async def test_video_note_считается_как_видео(self) -> None:
        """video_note — Video: 1."""
        bot = _make_bot()
        msg = _make_message()
        vnote = MagicMock()
        msgs = [_make_msg_obj(text=None, video_note=vnote)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Video: 1" in text

    @pytest.mark.asyncio
    async def test_voice_считается(self) -> None:
        """1 голосовое — Voice: 1."""
        bot = _make_bot()
        msg = _make_message()
        vc = MagicMock()
        msgs = [_make_msg_obj(text=None, voice=vc)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Voice: 1" in text

    @pytest.mark.asyncio
    async def test_audio_считается_как_voice(self) -> None:
        """audio — Voice: 1."""
        bot = _make_bot()
        msg = _make_message()
        au = MagicMock()
        msgs = [_make_msg_obj(text=None, audio=au)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Voice: 1" in text

    @pytest.mark.asyncio
    async def test_документ_считается(self) -> None:
        """1 документ — Docs: 1."""
        bot = _make_bot()
        msg = _make_message()
        doc = MagicMock()
        msgs = [_make_msg_obj(text=None, document=doc)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Docs: 1" in text

    @pytest.mark.asyncio
    async def test_прочие_считаются(self) -> None:
        """Сообщение без text/photo/video/voice/doc — Other: 1."""
        bot = _make_bot()
        msg = _make_message()
        # Ничего не задаём — все None/falsy
        msgs = [_make_msg_obj(text=None, photo=None, video=None,
                              video_note=None, voice=None, audio=None,
                              document=None)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Other: 1" in text

    @pytest.mark.asyncio
    async def test_смешанные_типы(self) -> None:
        """3 текста + 2 фото + 1 видео — проверяем каждый счётчик."""
        bot = _make_bot()
        msg = _make_message()
        ph = MagicMock()
        vid = MagicMock()
        msgs = (
            [_make_msg_obj(text=f"t{i}") for i in range(3)]
            + [_make_msg_obj(text=None, photo=ph) for _ in range(2)]
            + [_make_msg_obj(text=None, video=vid)]
        )
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Text: 3" in text
        assert "Photo: 2" in text
        assert "Video: 1" in text


# ---------------------------------------------------------------------------
# Тесты: даты и статистика по дням
# ---------------------------------------------------------------------------


class TestHistoryDates:
    """Проверяем работу с датами сообщений."""

    @pytest.mark.asyncio
    async def test_первая_дата_отображается(self) -> None:
        """First: содержит дату первого сообщения."""
        bot = _make_bot()
        msg = _make_message()
        dt = datetime.datetime(2024, 1, 15, 10, 0, 0, tzinfo=datetime.timezone.utc)
        msgs = [_make_msg_obj(date=dt)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "2024-01-15" in text

    @pytest.mark.asyncio
    async def test_последняя_дата_отображается(self) -> None:
        """Last: содержит дату последнего сообщения."""
        bot = _make_bot()
        msg = _make_message()
        dt_old = datetime.datetime(2024, 1, 15, tzinfo=datetime.timezone.utc)
        dt_new = datetime.datetime(2026, 4, 12, tzinfo=datetime.timezone.utc)
        msgs = [_make_msg_obj(date=dt_old), _make_msg_obj(date=dt_new)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "2026-04-12" in text

    @pytest.mark.asyncio
    async def test_first_less_than_last(self) -> None:
        """First date <= Last date."""
        bot = _make_bot()
        msg = _make_message()
        dt_early = datetime.datetime(2023, 3, 1, tzinfo=datetime.timezone.utc)
        dt_late = datetime.datetime(2025, 11, 20, tzinfo=datetime.timezone.utc)
        msgs = [_make_msg_obj(date=dt_late), _make_msg_obj(date=dt_early)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        # Оба должны быть в ответе
        assert "2023-03-01" in text
        assert "2025-11-20" in text

    @pytest.mark.asyncio
    async def test_самый_активный_день_wednesday(self) -> None:
        """3 сообщения в среду (weekday=2) → 'Wednesday'."""
        bot = _make_bot()
        msg = _make_message()
        # 2024-01-17 — среда
        wed = datetime.datetime(2024, 1, 17, 10, 0, tzinfo=datetime.timezone.utc)
        # 2024-01-15 — понедельник
        mon = datetime.datetime(2024, 1, 15, 10, 0, tzinfo=datetime.timezone.utc)
        msgs = (
            [_make_msg_obj(date=wed)] * 3
            + [_make_msg_obj(date=mon)]
        )
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Wednesday" in text

    @pytest.mark.asyncio
    async def test_среднее_в_день_один_день(self) -> None:
        """5 сообщений за один день → Average: 5 msgs/day."""
        bot = _make_bot()
        msg = _make_message()
        dt = datetime.datetime(2024, 6, 5, 12, 0, tzinfo=datetime.timezone.utc)
        msgs = [_make_msg_obj(date=dt) for _ in range(5)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Average: 5 msgs/day" in text

    @pytest.mark.asyncio
    async def test_среднее_в_день_два_дня(self) -> None:
        """6 сообщений за 2 дня → Average: 3 msgs/day."""
        bot = _make_bot()
        msg = _make_message()
        dt1 = datetime.datetime(2024, 6, 5, 12, 0, tzinfo=datetime.timezone.utc)
        dt2 = datetime.datetime(2024, 6, 6, 12, 0, tzinfo=datetime.timezone.utc)
        msgs = (
            [_make_msg_obj(date=dt1)] * 3
            + [_make_msg_obj(date=dt2)] * 3
        )
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Average: 3 msgs/day" in text

    @pytest.mark.asyncio
    async def test_дата_как_int_timestamp(self) -> None:
        """date как int (unix timestamp) — парсится корректно."""
        bot = _make_bot()
        msg = _make_message()
        # 2024-01-15 00:00:00 UTC = 1705276800
        m = _make_msg_obj(date=1705276800)
        bot.client.get_chat_history = MagicMock(return_value=_aiter([m]))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "2024-01-15" in text

    @pytest.mark.asyncio
    async def test_сообщение_без_даты_не_ломает_подсчёт(self) -> None:
        """Сообщение с date=None — не вызывает исключения."""
        bot = _make_bot()
        msg = _make_message()
        m = _make_msg_obj(date=None)
        m.date = None
        bot.client.get_chat_history = MagicMock(return_value=_aiter([m]))

        # Не должно упасть
        await handle_history(bot, msg)

        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: пустой чат и обработка ошибок
# ---------------------------------------------------------------------------


class TestHistoryEdgeCases:
    """Граничные случаи."""

    @pytest.mark.asyncio
    async def test_пустой_чат_не_показывает_stats(self) -> None:
        """При 0 сообщений — сообщение о пустом чате."""
        bot = _make_bot()
        msg = _make_message()
        bot.client.get_chat_history = MagicMock(return_value=_aiter([]))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "нет сообщений" in text.lower() or "Chat History Stats" not in text

    @pytest.mark.asyncio
    async def test_ошибка_get_chat_history_бросает_user_input_error(self) -> None:
        """При ошибке get_chat_history — UserInputError."""
        bot = _make_bot()
        msg = _make_message()

        async def _broken_gen():
            raise Exception("forbidden")
            # Нужен yield чтобы функция была generator
            yield  # type: ignore[misc]

        bot.client.get_chat_history = MagicMock(return_value=_broken_gen())

        with pytest.raises(UserInputError) as exc_info:
            await handle_history(bot, msg)

        assert "Не удалось" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_запрашивается_правильный_chat_id(self) -> None:
        """get_chat_history вызывается с chat_id из текущего чата."""
        bot = _make_bot()
        msg = _make_message(chat_id=-9991234567)
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        bot.client.get_chat_history.assert_called_once_with(-9991234567, limit=1000)

    @pytest.mark.asyncio
    async def test_limit_1000(self) -> None:
        """get_chat_history вызывается с limit=1000."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj()]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        _, kwargs = bot.client.get_chat_history.call_args
        assert kwargs.get("limit") == 1000 or bot.client.get_chat_history.call_args[0][1] == 1000

    @pytest.mark.asyncio
    async def test_одно_сообщение_all_types_zero_except_text(self) -> None:
        """Одно текстовое: Photo, Video, Voice, Docs, Other — все 0."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj(text="hi")]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Text: 1" in text
        assert "Photo: 0" in text
        assert "Video: 0" in text
        assert "Voice: 0" in text
        assert "Docs: 0" in text
        assert "Other: 0" in text

    @pytest.mark.asyncio
    async def test_тысячный_разделитель_в_messages(self) -> None:
        """1000 сообщений — Messages: 1,000."""
        bot = _make_bot()
        msg = _make_message()
        msgs = [_make_msg_obj() for _ in range(1000)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Messages: 1,000" in text

    @pytest.mark.asyncio
    async def test_все_дни_недели_распознаются(self) -> None:
        """Сообщения в каждый день недели — weekday правильно определяется."""
        bot = _make_bot()
        msg = _make_message()
        # Mon=0, Sun=6
        # 2024-01-21 — воскресенье (weekday=6, больше всего)
        sun = datetime.datetime(2024, 1, 21, 10, 0, tzinfo=datetime.timezone.utc)
        mon = datetime.datetime(2024, 1, 22, 10, 0, tzinfo=datetime.timezone.utc)
        msgs = [_make_msg_obj(date=sun)] * 5 + [_make_msg_obj(date=mon)]
        bot.client.get_chat_history = MagicMock(return_value=_aiter(msgs))

        await handle_history(bot, msg)

        text: str = msg.reply.call_args[0][0]
        assert "Sunday" in text


# ---------------------------------------------------------------------------
# Тесты: экспорт хендлера
# ---------------------------------------------------------------------------


class TestHistoryExport:
    """Проверяем, что handle_history правильно экспортируется."""

    def test_handle_history_импортируется_из_command_handlers(self) -> None:
        """handle_history доступна в command_handlers."""
        from src.handlers.command_handlers import handle_history as _h
        assert callable(_h)

    def test_handle_history_импортируется_из_handlers_init(self) -> None:
        """handle_history доступна через src.handlers."""
        from src.handlers import handle_history as _h
        assert callable(_h)

    def test_handle_history_в_all(self) -> None:
        """handle_history присутствует в __all__ пакета handlers."""
        import src.handlers as _pkg
        assert "handle_history" in _pkg.__all__
