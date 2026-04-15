# -*- coding: utf-8 -*-
"""
Юнит-тесты для !grep command handler.

Покрываем:
  - Валидация пустого запроса → UserInputError
  - Парсинг аргументов: query, @chat, N
  - Regex-поиск /pattern/
  - Невалидный regex → UserInputError
  - Совпадения найдены: корректный формат ответа
  - Совпадений нет: «Ничего не найдено»
  - Лимит 20 совпадений
  - Ограничение длинного preview (>200 символов)
  - Ошибка при итерации по истории
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_grep


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_tg_message(
    text: str,
    username: str = "testuser",
    date: datetime.datetime | None = None,
) -> SimpleNamespace:
    """Создаёт stub Telegram-сообщения для get_chat_history."""
    if date is None:
        date = datetime.datetime(2026, 4, 12, 14, 30, 0)
    return SimpleNamespace(
        text=text,
        caption=None,
        date=date,
        from_user=SimpleNamespace(
            username=username,
            first_name="Test",
        ),
        sender_chat=None,
    )


async def _async_history(*msgs: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    """AsyncGenerator из набора сообщений."""
    for m in msgs:
        yield m


def _make_bot_and_message(
    command_args: str,
    chat_id: int = 12345,
    history_msgs: tuple | None = None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Создаёт (bot, message) stubs для тестов handle_grep."""
    edit_mock = AsyncMock()
    sent_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!grep {command_args}".strip(),
        reply=AsyncMock(return_value=sent_msg),
        chat=SimpleNamespace(id=chat_id),
    )

    # Имитируем get_chat_history: возвращает AsyncGenerator
    async def _fake_history(target_chat, limit):  # noqa: ANN001
        if history_msgs:
            for m in history_msgs:
                yield m

    client = MagicMock()
    client.get_chat_history = _fake_history

    bot = SimpleNamespace(
        _get_command_args=lambda _m: command_args,
        client=client,
    )
    return bot, msg


def _get_edit_text(msg: SimpleNamespace) -> str:
    """Возвращает текст последнего вызова edit()."""
    return msg.reply.return_value.edit.call_args[0][0]


# ===========================================================================
# Валидация входных данных
# ===========================================================================


class TestHandleGrepValidation:
    """Проверка обязательных условий для !grep."""

    @pytest.mark.asyncio
    async def test_пустой_запрос_бросает_UserInputError(self) -> None:
        bot, msg = _make_bot_and_message("")
        with pytest.raises(UserInputError):
            await handle_grep(bot, msg)

    @pytest.mark.asyncio
    async def test_только_chat_без_query_бросает_UserInputError(self) -> None:
        bot, msg = _make_bot_and_message("@durov")
        with pytest.raises(UserInputError):
            await handle_grep(bot, msg)

    @pytest.mark.asyncio
    async def test_только_число_без_query_бросает_UserInputError(self) -> None:
        bot, msg = _make_bot_and_message("500")
        with pytest.raises(UserInputError):
            await handle_grep(bot, msg)

    @pytest.mark.asyncio
    async def test_невалидный_regex_бросает_UserInputError(self) -> None:
        bot, msg = _make_bot_and_message("/[invalid/")
        with pytest.raises(UserInputError) as exc_info:
            await handle_grep(bot, msg)
        assert "regex" in exc_info.value.user_message.lower() or "невалидный" in exc_info.value.user_message.lower()


# ===========================================================================
# Парсинг аргументов
# ===========================================================================


class TestHandleGrepArgParsing:
    """Проверка парсинга аргументов команды."""

    @pytest.mark.asyncio
    async def test_простой_query(self) -> None:
        """!grep биткоин — ищет в текущем чате с лимитом 200."""
        history = (_make_tg_message("купить биткоин"),)
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "биткоин" in edit_text.lower() or "Найдено" in edit_text

    @pytest.mark.asyncio
    async def test_query_с_лимитом(self) -> None:
        """!grep биткоин 500 — парсит лимит правильно."""
        history = (_make_tg_message("биткоин растёт"),)
        bot, msg = _make_bot_and_message("биткоин 500", history_msgs=history)
        await handle_grep(bot, msg)
        # Не падает, находит совпадение
        edit_text = _get_edit_text(msg)
        assert "Найдено" in edit_text

    @pytest.mark.asyncio
    async def test_query_с_chat_и_лимитом(self) -> None:
        """!grep биткоин @durov 100 — парсит chat и лимит."""
        history = (_make_tg_message("биткоин"),)
        bot, msg = _make_bot_and_message("биткоин @durov 100", chat_id=99999, history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено" in edit_text

    @pytest.mark.asyncio
    async def test_лимит_не_больше_2000(self) -> None:
        """Лимит 9999 обрезается до 2000 (нет исключений)."""
        bot, msg = _make_bot_and_message("тест 9999", history_msgs=())
        await handle_grep(bot, msg)  # не должно падать


# ===========================================================================
# Поиск: совпадения
# ===========================================================================


class TestHandleGrepMatches:
    """Проверка корректной работы поиска."""

    @pytest.mark.asyncio
    async def test_plain_поиск_case_insensitive(self) -> None:
        """Поиск case-insensitive: «Биткоин» находит «биткоин»."""
        history = (
            _make_tg_message("купить биткоин срочно"),
            _make_tg_message("продать эфир"),
        )
        bot, msg = _make_bot_and_message("Биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **1**" in edit_text

    @pytest.mark.asyncio
    async def test_несколько_совпадений(self) -> None:
        """Несколько совпадений — все попадают в результат."""
        history = (
            _make_tg_message("биткоин вверх"),
            _make_tg_message("биткоин вниз"),
            _make_tg_message("эфир сейчас"),
            _make_tg_message("биткоин боковик"),
        )
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **3**" in edit_text

    @pytest.mark.asyncio
    async def test_ничего_не_найдено(self) -> None:
        """Если совпадений нет — «Ничего не найдено»."""
        history = (
            _make_tg_message("кошки и собаки"),
            _make_tg_message("погода хорошая"),
        )
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Ничего не найдено" in edit_text

    @pytest.mark.asyncio
    async def test_пустая_история(self) -> None:
        """Пустая история → «Ничего не найдено»."""
        bot, msg = _make_bot_and_message("биткоин", history_msgs=())
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Ничего не найдено" in edit_text

    @pytest.mark.asyncio
    async def test_только_caption_без_text(self) -> None:
        """Сообщения с caption (фото/видео) тоже ищутся."""
        caption_msg = SimpleNamespace(
            text=None,
            caption="биткоин на фото",
            date=datetime.datetime(2026, 4, 12, 10, 0, 0),
            from_user=SimpleNamespace(username="photo_user", first_name="Photo"),
            sender_chat=None,
        )
        history = (caption_msg,)
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **1**" in edit_text

    @pytest.mark.asyncio
    async def test_сообщения_без_текста_пропускаются(self) -> None:
        """Медиа без текста и caption не вызывают ошибок."""
        no_text_msg = SimpleNamespace(
            text=None,
            caption=None,
            date=datetime.datetime(2026, 4, 12, 9, 0, 0),
            from_user=SimpleNamespace(username="user1", first_name="User"),
            sender_chat=None,
        )
        history = (no_text_msg,)
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Ничего не найдено" in edit_text


# ===========================================================================
# Regex-поиск
# ===========================================================================


class TestHandleGrepRegex:
    """Проверка regex-режима /pattern/."""

    @pytest.mark.asyncio
    async def test_regex_поиск(self) -> None:
        """!grep /бит.оин/ — regex case-insensitive."""
        history = (
            _make_tg_message("купить биткоин"),
            _make_tg_message("продать эфир"),
        )
        bot, msg = _make_bot_and_message("/бит.оин/", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **1**" in edit_text

    @pytest.mark.asyncio
    async def test_regex_case_insensitive(self) -> None:
        """Regex флаг IGNORECASE работает."""
        history = (
            _make_tg_message("Биткоин сегодня"),
            _make_tg_message("БИТКОИН завтра"),
        )
        bot, msg = _make_bot_and_message("/биткоин/", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **2**" in edit_text

    @pytest.mark.asyncio
    async def test_regex_альтернация(self) -> None:
        """Regex с | (ИЛИ) работает корректно."""
        history = (
            _make_tg_message("биткоин растёт"),
            _make_tg_message("эфир падает"),
            _make_tg_message("рубль стабилен"),
        )
        bot, msg = _make_bot_and_message("/биткоин|эфир/", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Найдено **2**" in edit_text

    @pytest.mark.asyncio
    async def test_display_query_для_regex(self) -> None:
        """В ответе отображается /pattern/ как display_query."""
        history = (_make_tg_message("биткоин"),)
        bot, msg = _make_bot_and_message("/биткоин/", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "/биткоин/" in edit_text


# ===========================================================================
# Форматирование результата
# ===========================================================================


class TestHandleGrepFormatting:
    """Проверка форматирования ответа."""

    @pytest.mark.asyncio
    async def test_формат_строки_результата(self) -> None:
        """Каждая строка содержит [дата] @username: текст."""
        history = (_make_tg_message("биткоин сейчас", username="trader42"),)
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "@trader42" in edit_text
        assert "12.04" in edit_text  # дата из _make_tg_message

    @pytest.mark.asyncio
    async def test_sender_без_username(self) -> None:
        """Если username не задан — используется first_name."""
        msg_no_username = SimpleNamespace(
            text="биткоин",
            caption=None,
            date=datetime.datetime(2026, 4, 12, 10, 0),
            from_user=SimpleNamespace(username=None, first_name="Анонимус"),
            sender_chat=None,
        )
        bot, msg = _make_bot_and_message("биткоин", history_msgs=(msg_no_username,))
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "Анонимус" in edit_text

    @pytest.mark.asyncio
    async def test_sender_chat_канал(self) -> None:
        """Для сообщений из канала — используется title канала."""
        channel_msg = SimpleNamespace(
            text="биткоин от канала",
            caption=None,
            date=datetime.datetime(2026, 4, 12, 11, 0),
            from_user=None,
            sender_chat=SimpleNamespace(title="CryptoNews"),
        )
        bot, msg = _make_bot_and_message("биткоин", history_msgs=(channel_msg,))
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "CryptoNews" in edit_text

    @pytest.mark.asyncio
    async def test_нумерация_строк(self) -> None:
        """Результаты пронумерованы: 1. ... 2. ..."""
        history = (
            _make_tg_message("биткоин раз"),
            _make_tg_message("биткоин два"),
        )
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "1." in edit_text
        assert "2." in edit_text

    @pytest.mark.asyncio
    async def test_preview_обрезается_у_длинных_сообщений(self) -> None:
        """Длинные сообщения обрезаются до ~200 символов."""
        long_text = "биткоин " + "x" * 500
        history = (_make_tg_message(long_text),)
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        # Результат не должен содержать все 500 x-ов
        assert edit_text.count("x") < 500

    @pytest.mark.asyncio
    async def test_лимит_20_совпадений(self) -> None:
        """Не более 20 совпадений в ответе."""
        history = tuple(_make_tg_message(f"биткоин #{i}") for i in range(30))
        bot, msg = _make_bot_and_message("биткоин", history_msgs=history)
        await handle_grep(bot, msg)
        edit_text = _get_edit_text(msg)
        assert "первые 20" in edit_text
        assert "Найдено **20**" in edit_text


# ===========================================================================
# Обработка ошибок
# ===========================================================================


class TestHandleGrepErrors:
    """Проверка обработки ошибок при поиске."""

    @pytest.mark.asyncio
    async def test_ошибка_get_chat_history_graceful(self) -> None:
        """Ошибка при итерации → graceful ответ с ❌."""
        async def _error_history(target_chat, limit):  # noqa: ANN001
            raise RuntimeError("Telegram API недоступен")
            yield  # делает функцию генератором

        client = MagicMock()
        client.get_chat_history = _error_history

        edit_mock = AsyncMock()
        sent_msg = SimpleNamespace(edit=edit_mock)
        msg = SimpleNamespace(
            text="!grep биткоин",
            reply=AsyncMock(return_value=sent_msg),
            chat=SimpleNamespace(id=12345),
        )
        bot = SimpleNamespace(
            _get_command_args=lambda _m: "биткоин",
            client=client,
        )
        await handle_grep(bot, msg)
        edit_text = edit_mock.call_args[0][0]
        assert "❌" in edit_text
