# -*- coding: utf-8 -*-
"""
Тесты команд !poll и !quiz (handle_poll, handle_quiz) из src/handlers/command_handlers.py.

Покрытие:
- handle_poll: базовый опрос с вопросом и вариантами
- handle_poll: анонимный режим (anonymous prefix)
- handle_poll: минимум 2 варианта (меньше → UserInputError)
- handle_poll: максимум 10 вариантов (больше → UserInputError)
- handle_poll: пустые аргументы → UserInputError с help-текстом
- handle_poll: удаление команды после успеха (best-effort)
- handle_poll: ошибка удаления команды поглощается
- handle_quiz: базовый квиз, первый вариант — правильный
- handle_quiz: менее 2 вариантов → UserInputError
- handle_quiz: более 10 вариантов → UserInputError
- handle_quiz: пустые аргументы → UserInputError с help-текстом
- handle_quiz: correct_option_id=0 всегда
- handle_quiz: type="quiz" передаётся в send_poll
- handle_quiz: is_anonymous=False всегда
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_poll, handle_quiz


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_poll = AsyncMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(chat_id: int = 100) -> MagicMock:
    msg = MagicMock()
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    return msg


# ===========================================================================
# handle_poll
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_poll_basic() -> None:
    """Базовый опрос: send_poll вызывается с корректными параметрами."""
    bot = _make_bot("Любимый цвет? | Красный | Синий | Зелёный")
    message = _make_message(chat_id=200)

    await handle_poll(bot, message)

    bot.client.send_poll.assert_awaited_once_with(
        chat_id=200,
        question="Любимый цвет?",
        options=["Красный", "Синий", "Зелёный"],
        is_anonymous=False,
    )


@pytest.mark.asyncio
async def test_handle_poll_two_options() -> None:
    """Минимально допустимый опрос — 2 варианта."""
    bot = _make_bot("Да или нет? | Да | Нет")
    message = _make_message()

    await handle_poll(bot, message)

    bot.client.send_poll.assert_awaited_once()
    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["options"] == ["Да", "Нет"]


@pytest.mark.asyncio
async def test_handle_poll_anonymous_prefix() -> None:
    """Префикс 'anonymous' → is_anonymous=True, убирается из вопроса."""
    bot = _make_bot("anonymous Лучший язык? | Python | Rust | Go")
    message = _make_message()

    await handle_poll(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["is_anonymous"] is True
    assert call_kwargs["question"] == "Лучший язык?"
    assert call_kwargs["options"] == ["Python", "Rust", "Go"]


@pytest.mark.asyncio
async def test_handle_poll_anonymous_false_by_default() -> None:
    """Без anonymous-префикса опрос не анонимный."""
    bot = _make_bot("Вопрос? | А | Б")
    message = _make_message()

    await handle_poll(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["is_anonymous"] is False


@pytest.mark.asyncio
async def test_handle_poll_too_few_options_raises() -> None:
    """Только 1 вариант → UserInputError."""
    bot = _make_bot("Вопрос? | Один")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_poll(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_poll_no_separator_raises() -> None:
    """Нет разделителей → UserInputError (нет вариантов)."""
    bot = _make_bot("Просто текст без вариантов")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_poll(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_poll_too_many_options_raises() -> None:
    """11 вариантов → UserInputError."""
    options = " | ".join(f"Вариант {i}" for i in range(1, 12))
    bot = _make_bot(f"Вопрос? | {options}")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_poll(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_poll_exactly_ten_options() -> None:
    """Ровно 10 вариантов — допустимо."""
    options = " | ".join(f"Вариант {i}" for i in range(1, 11))
    bot = _make_bot(f"Вопрос? | {options}")
    message = _make_message()

    await handle_poll(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert len(call_kwargs["options"]) == 10


@pytest.mark.asyncio
async def test_handle_poll_empty_args_raises() -> None:
    """Пустые аргументы → UserInputError с help-текстом."""
    bot = _make_bot("")
    message = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_poll(bot, message)

    assert "poll" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_handle_poll_help_keyword_raises() -> None:
    """Аргумент 'help' → UserInputError с help-текстом."""
    bot = _make_bot("help")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_poll(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_poll_deletes_command_message() -> None:
    """После успеха команда удаляется."""
    bot = _make_bot("Вопрос? | А | Б")
    message = _make_message()

    await handle_poll(bot, message)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_poll_delete_failure_silenced() -> None:
    """Ошибка при удалении команды поглощается — опрос всё равно отправляется."""
    bot = _make_bot("Вопрос? | А | Б")
    message = _make_message()
    message.delete = AsyncMock(side_effect=Exception("ACCESS_DENIED"))

    # Не должно бросать исключение
    await handle_poll(bot, message)

    bot.client.send_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_poll_strips_whitespace_in_options() -> None:
    """Пробелы вокруг вариантов убираются."""
    bot = _make_bot("Вопрос? |  Вариант A  |  Вариант B  ")
    message = _make_message()

    await handle_poll(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["options"] == ["Вариант A", "Вариант B"]


@pytest.mark.asyncio
async def test_handle_poll_question_preserved() -> None:
    """Вопрос передаётся как есть (без изменений)."""
    bot = _make_bot("Какой самый длинный вопрос? | А | Б")
    message = _make_message()

    await handle_poll(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["question"] == "Какой самый длинный вопрос?"


# ===========================================================================
# handle_quiz
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_quiz_basic() -> None:
    """Базовый квиз: send_poll вызывается с type='quiz' и correct_option_id=0."""
    bot = _make_bot("Столица России? | Москва | Петербург | Казань")
    message = _make_message(chat_id=300)

    await handle_quiz(bot, message)

    bot.client.send_poll.assert_awaited_once_with(
        chat_id=300,
        question="Столица России?",
        options=["Москва", "Петербург", "Казань"],
        type="quiz",
        correct_option_id=0,
        is_anonymous=False,
    )


@pytest.mark.asyncio
async def test_handle_quiz_correct_option_always_zero() -> None:
    """correct_option_id всегда 0 (первый вариант — правильный)."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный 1 | Неправильный 2")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["correct_option_id"] == 0
    assert call_kwargs["options"][0] == "Правильный"


@pytest.mark.asyncio
async def test_handle_quiz_type_is_quiz() -> None:
    """type='quiz' передаётся в send_poll."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["type"] == "quiz"


@pytest.mark.asyncio
async def test_handle_quiz_is_not_anonymous() -> None:
    """Квизы всегда не анонимные."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["is_anonymous"] is False


@pytest.mark.asyncio
async def test_handle_quiz_two_options_minimum() -> None:
    """Минимум 2 варианта для квиза."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert len(call_kwargs["options"]) == 2


@pytest.mark.asyncio
async def test_handle_quiz_one_option_raises() -> None:
    """Один вариант → UserInputError."""
    bot = _make_bot("Вопрос? | Правильный")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_quiz(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_quiz_eleven_options_raises() -> None:
    """11 вариантов → UserInputError."""
    options = " | ".join(f"Вариант {i}" for i in range(1, 12))
    bot = _make_bot(f"Вопрос? | {options}")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_quiz(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_quiz_exactly_ten_options() -> None:
    """Ровно 10 вариантов допустимо."""
    options = " | ".join(f"Вариант {i}" for i in range(1, 11))
    bot = _make_bot(f"Вопрос? | {options}")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert len(call_kwargs["options"]) == 10


@pytest.mark.asyncio
async def test_handle_quiz_empty_args_raises() -> None:
    """Пустые аргументы → UserInputError с help-текстом."""
    bot = _make_bot("")
    message = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_quiz(bot, message)

    assert "quiz" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_handle_quiz_help_keyword_raises() -> None:
    """Аргумент 'помощь' → UserInputError."""
    bot = _make_bot("помощь")
    message = _make_message()

    with pytest.raises(UserInputError):
        await handle_quiz(bot, message)

    bot.client.send_poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_quiz_deletes_command_message() -> None:
    """После успеха команда удаляется."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный")
    message = _make_message()

    await handle_quiz(bot, message)

    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_quiz_delete_failure_silenced() -> None:
    """Ошибка при удалении поглощается — квиз всё равно отправляется."""
    bot = _make_bot("Вопрос? | Правильный | Неправильный")
    message = _make_message()
    message.delete = AsyncMock(side_effect=Exception("NO_PERMISSION"))

    await handle_quiz(bot, message)

    bot.client.send_poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_quiz_strips_whitespace_in_options() -> None:
    """Пробелы вокруг вариантов убираются."""
    bot = _make_bot("Вопрос? |  Правильный  |  Неправильный  ")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["options"] == ["Правильный", "Неправильный"]


@pytest.mark.asyncio
async def test_handle_quiz_question_stripped() -> None:
    """Вопрос передаётся точно, первый элемент split по |."""
    bot = _make_bot("Сколько планет в Солнечной системе? | 8 | 9 | 7")
    message = _make_message()

    await handle_quiz(bot, message)

    call_kwargs = bot.client.send_poll.call_args.kwargs
    assert call_kwargs["question"] == "Сколько планет в Солнечной системе?"
    assert call_kwargs["options"][0] == "8"
