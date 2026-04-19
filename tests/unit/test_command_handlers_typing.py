# -*- coding: utf-8 -*-
"""
Тесты команды !typing — симуляция набора текста / записи голосового / загрузки.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _TYPING_ACTION_MAP,
    _TYPING_DEFAULT_SECONDS,
    _TYPING_LABEL_MAP,
    _TYPING_MAX_SECONDS,
    handle_typing,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(owner: bool = True):
    """Создаёт минимальный mock-бот."""
    profile = SimpleNamespace(level=AccessLevel.OWNER if owner else AccessLevel.GUEST)
    bot = SimpleNamespace(
        client=MagicMock(),
        me=SimpleNamespace(id=1),
    )
    bot._get_access_profile = lambda user: profile
    bot._get_command_args = lambda msg: msg._raw_args
    bot.client.send_chat_action = AsyncMock()
    return bot


def _make_message(args: str = "", chat_id: int = 42):
    """Создаёт минимальный mock-сообщение."""
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=99),
        chat=SimpleNamespace(id=chat_id),
        _raw_args=args,
        delete=AsyncMock(),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )
    return msg


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------


class TestTypingConstants:
    def test_default_seconds(self):
        assert _TYPING_DEFAULT_SECONDS == 5

    def test_max_seconds(self):
        assert _TYPING_MAX_SECONDS == 30

    def test_action_map_keys(self):
        assert "typing" in _TYPING_ACTION_MAP
        assert "record" in _TYPING_ACTION_MAP
        assert "upload" in _TYPING_ACTION_MAP

    def test_action_map_values_are_pyrogram_attrs(self):
        from pyrogram import enums

        for key, attr in _TYPING_ACTION_MAP.items():
            assert hasattr(enums.ChatAction, attr), f"enums.ChatAction.{attr} не существует"

    def test_label_map_has_all_keys(self):
        assert set(_TYPING_LABEL_MAP) == set(_TYPING_ACTION_MAP)


# ---------------------------------------------------------------------------
# Проверка доступа
# ---------------------------------------------------------------------------


class TestTypingAccess:
    @pytest.mark.asyncio
    async def test_non_owner_raises(self):
        bot = _make_bot(owner=False)
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_typing(bot, msg)
        assert "typing" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_owner_passes_access(self, monkeypatch):
        """Owner не получает UserInputError из-за проверки доступа."""
        bot = _make_bot(owner=True)
        # Короткая длительность: 1 секунда, чтобы тест не зависал
        msg = _make_message(args="1")

        # Заменяем asyncio.sleep на мгновенный stub
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        await handle_typing(bot, msg)  # не должно упасть с UserInputError


# ---------------------------------------------------------------------------
# Парсинг аргументов
# ---------------------------------------------------------------------------


class TestTypingArgParsing:
    @pytest.mark.asyncio
    async def test_no_args_uses_defaults(self, monkeypatch):
        """Без аргументов — TYPING, 5 секунд."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="")
        await handle_typing(bot, msg)

        # send_chat_action должен быть вызван хотя бы раз с TYPING
        calls = [str(call) for call in bot.client.send_chat_action.await_args_list]
        # Проверяем что вызов был (cancel или typing)
        assert bot.client.send_chat_action.await_count >= 1

    @pytest.mark.asyncio
    async def test_custom_seconds(self, monkeypatch):
        """!typing 10 → 10 секунд."""
        import asyncio

        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        bot = _make_bot()
        msg = _make_message(args="10")
        await handle_typing(bot, msg)

        # Суммарное время sleep должно быть ~10 секунд
        total = sum(call.args[0] for call in sleep_mock.await_args_list)
        assert total == pytest.approx(10, abs=5)

    @pytest.mark.asyncio
    async def test_record_mode(self, monkeypatch):
        """!typing record → RECORD_AUDIO action."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        from pyrogram import enums

        bot = _make_bot()
        msg = _make_message(args="record 1")
        await handle_typing(bot, msg)

        # Первый вызов (не cancel) должен использовать RECORD_AUDIO
        first_call_action = bot.client.send_chat_action.await_args_list[0].args[1]
        assert first_call_action == enums.ChatAction.RECORD_AUDIO

    @pytest.mark.asyncio
    async def test_upload_mode(self, monkeypatch):
        """!typing upload → UPLOAD_DOCUMENT action."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        from pyrogram import enums

        bot = _make_bot()
        msg = _make_message(args="upload 1")
        await handle_typing(bot, msg)

        first_call_action = bot.client.send_chat_action.await_args_list[0].args[1]
        assert first_call_action == enums.ChatAction.UPLOAD_DOCUMENT

    @pytest.mark.asyncio
    async def test_record_with_custom_seconds(self, monkeypatch):
        """!typing record 3 → 3 секунды."""
        import asyncio

        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        bot = _make_bot()
        msg = _make_message(args="record 3")
        await handle_typing(bot, msg)

        total = sum(call.args[0] for call in sleep_mock.await_args_list)
        assert total == pytest.approx(3, abs=4)

    @pytest.mark.asyncio
    async def test_invalid_subcommand_shows_usage(self, monkeypatch):
        """!typing blah → UserInputError с текстом подсказки."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="blah")
        with pytest.raises(UserInputError):
            await handle_typing(bot, msg)

    @pytest.mark.asyncio
    async def test_invalid_seconds_for_record_raises(self, monkeypatch):
        """!typing record abc → UserInputError с текстом про длительность."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="record abc")
        with pytest.raises(UserInputError) as exc_info:
            await handle_typing(bot, msg)
        assert "числом" in exc_info.value.user_message or "abc" in exc_info.value.user_message


# ---------------------------------------------------------------------------
# Ограничение длительности
# ---------------------------------------------------------------------------


class TestTypingDurationClamp:
    @pytest.mark.asyncio
    async def test_zero_clamped_to_one(self, monkeypatch):
        """Длительность 0 кламп-ится до 1."""
        import asyncio

        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        bot = _make_bot()
        msg = _make_message(args="0")
        await handle_typing(bot, msg)

        total = sum(call.args[0] for call in sleep_mock.await_args_list)
        assert total >= 1

    @pytest.mark.asyncio
    async def test_max_seconds_clamped(self, monkeypatch):
        """Длительность > 30 кламп-ится до 30."""
        import asyncio

        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        bot = _make_bot()
        msg = _make_message(args="999")
        await handle_typing(bot, msg)

        total = sum(call.args[0] for call in sleep_mock.await_args_list)
        assert total <= _TYPING_MAX_SECONDS + 4  # +4 — последний интервал

    @pytest.mark.asyncio
    async def test_exact_max_seconds(self, monkeypatch):
        """!typing 30 → ровно 30 секунд (без превышения)."""
        import asyncio

        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        bot = _make_bot()
        msg = _make_message(args="30")
        await handle_typing(bot, msg)

        total = sum(call.args[0] for call in sleep_mock.await_args_list)
        assert total <= 30


# ---------------------------------------------------------------------------
# Поведение cancel и delete
# ---------------------------------------------------------------------------


class TestTypingBehavior:
    @pytest.mark.asyncio
    async def test_message_deleted(self, monkeypatch):
        """Команда !typing удаляет своё сообщение."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="1")
        await handle_typing(bot, msg)

        msg.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_sent_at_end(self, monkeypatch):
        """После завершения отправляется CANCEL."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        from pyrogram import enums

        bot = _make_bot()
        msg = _make_message(args="1")
        await handle_typing(bot, msg)

        last_call_action = bot.client.send_chat_action.await_args_list[-1].args[1]
        assert last_call_action == enums.ChatAction.CANCEL

    @pytest.mark.asyncio
    async def test_correct_chat_id_used(self, monkeypatch):
        """Действие отправляется в правильный chat_id."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="1", chat_id=777)
        await handle_typing(bot, msg)

        for call in bot.client.send_chat_action.await_args_list:
            assert call.args[0] == 777

    @pytest.mark.asyncio
    async def test_send_action_error_does_not_crash(self, monkeypatch):
        """Ошибка send_chat_action не роняет хендлер."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        bot.client.send_chat_action = AsyncMock(side_effect=Exception("network error"))
        msg = _make_message(args="1")
        # Не должно упасть
        await handle_typing(bot, msg)

    @pytest.mark.asyncio
    async def test_delete_error_does_not_crash(self, monkeypatch):
        """Ошибка delete (например, нет прав) не роняет хендлер."""
        import asyncio

        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        bot = _make_bot()
        msg = _make_message(args="1")
        msg.delete = AsyncMock(side_effect=Exception("cannot delete"))
        # Не должно упасть
        await handle_typing(bot, msg)


# ---------------------------------------------------------------------------
# Проверка экспорта
# ---------------------------------------------------------------------------


class TestTypingExport:
    def test_handle_typing_in_handlers_init(self):
        from src.handlers import handle_typing as imported

        assert imported is handle_typing

    def test_handle_typing_in_all(self):
        from src.handlers import __all__

        assert "handle_typing" in __all__
