# -*- coding: utf-8 -*-
"""
Тесты command handlers: !notify, !remind, !reminders, !cronstatus.

Проверяем:
- парсинг аргументов handle_notify (on/off/status)
- валидацию входных данных handle_remind
- поведение handle_remind при отключённом scheduler
- логику handle_reminders (пустой список / с данными)
- handle_cronstatus (форматирование статуса)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_message(text: str = "", chat_id: int = 100) -> MagicMock:
    """Создаёт мок Message с нужными атрибутами."""
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.reply = AsyncMock()
    return msg


def _make_bot(command_args: str = "", narration_enabled: bool = True) -> MagicMock:
    """Создаёт мок KraabUserbot."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


# ---------------------------------------------------------------------------
# handle_notify
# ---------------------------------------------------------------------------


class TestHandleNotify:
    """Тесты управления tool-уведомлениями через !notify."""

    @pytest.mark.asyncio
    async def test_notify_on_включает_уведомления(self) -> None:
        """!notify on должен установить TOOL_NARRATION_ENABLED=1 и ответить."""
        from src.handlers.command_handlers import handle_notify

        bot = _make_bot(command_args="on")
        msg = _make_message("!notify on")

        mock_cfg = MagicMock()
        with patch("src.handlers.commands.scheduler_commands.config"), patch("src.config.config", mock_cfg):
            # патчим импорт внутри функции
            with patch("src.config.config.update_setting"):
                # handle_notify делает `from ..config import config as _cfg` внутри тела
                await handle_notify(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "ON" in reply_text

    @pytest.mark.asyncio
    async def test_notify_off_отключает_уведомления(self) -> None:
        """!notify off должен установить TOOL_NARRATION_ENABLED=0."""
        from src.handlers.command_handlers import handle_notify

        bot = _make_bot(command_args="off")
        msg = _make_message("!notify off")

        with patch("src.handlers.commands.scheduler_commands.config"):
            await handle_notify(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "OFF" in reply_text

    @pytest.mark.asyncio
    async def test_notify_без_аргументов_возвращает_статус(self) -> None:
        """!notify без аргументов — показать текущий статус."""
        from src.handlers.command_handlers import handle_notify

        bot = _make_bot(command_args="")
        msg = _make_message("!notify")

        with patch("src.handlers.commands.scheduler_commands.config"):
            await handle_notify(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        # В статусе должны быть подсказки по командам
        assert "!notify on" in reply_text
        assert "!notify off" in reply_text

    @pytest.mark.asyncio
    async def test_notify_on_alias_1(self) -> None:
        """!notify 1 — алиас для включения (как '1')."""
        from src.handlers.command_handlers import handle_notify

        bot = _make_bot(command_args="1")
        msg = _make_message("!notify 1")

        with patch("src.handlers.commands.scheduler_commands.config"):
            await handle_notify(bot, msg)

        msg.reply.assert_called_once()
        assert "ON" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_notify_off_alias_false(self) -> None:
        """!notify false — алиас для отключения."""
        from src.handlers.command_handlers import handle_notify

        bot = _make_bot(command_args="false")
        msg = _make_message("!notify false")

        with patch("src.handlers.commands.scheduler_commands.config"):
            await handle_notify(bot, msg)

        msg.reply.assert_called_once()
        assert "OFF" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# handle_remind — валидация входных данных
# ---------------------------------------------------------------------------


class TestHandleRemindValidation:
    """Тесты валидации парсинга аргументов !remind."""

    @pytest.mark.asyncio
    async def test_remind_без_аргументов_raises_user_input_error(self) -> None:
        """!remind без аргументов → UserInputError с подсказкой."""
        from src.handlers.command_handlers import handle_remind

        bot = _make_bot(command_args="")
        msg = _make_message("!remind")

        mock_cfg = MagicMock()
        mock_cfg.SCHEDULER_ENABLED = True

        with patch("src.handlers.commands.scheduler_commands.config", mock_cfg):
            with pytest.raises(UserInputError) as exc_info:
                await handle_remind(bot, msg)

        assert "Формат" in str(exc_info.value.user_message)

    @pytest.mark.asyncio
    async def test_remind_scheduler_disabled_raises_user_input_error(self) -> None:
        """Если SCHEDULER_ENABLED=False → UserInputError про отключённый scheduler."""
        from src.handlers.command_handlers import handle_remind

        bot = _make_bot(command_args="10m | тест")
        msg = _make_message("!remind 10m | тест")

        mock_cfg = MagicMock()
        mock_cfg.SCHEDULER_ENABLED = False

        with patch("src.handlers.commands.scheduler_commands.config", mock_cfg):
            with pytest.raises(UserInputError) as exc_info:
                await handle_remind(bot, msg)

        assert "SCHEDULER_ENABLED" in str(exc_info.value.user_message)

    @pytest.mark.asyncio
    async def test_remind_невалидный_формат_времени_raises_user_input_error(self) -> None:
        """Нераспознанный time_spec → UserInputError."""
        from src.handlers.command_handlers import handle_remind

        # split_reminder_input не поймёт этот формат → вернёт ('', text)
        bot = _make_bot(command_args="абракадабра без пайпа")
        msg = _make_message("!remind абракадабра без пайпа")

        mock_cfg = MagicMock()
        mock_cfg.SCHEDULER_ENABLED = True

        with patch("src.handlers.commands.scheduler_commands.config", mock_cfg):
            with pytest.raises(UserInputError):
                await handle_remind(bot, msg)

    @pytest.mark.asyncio
    async def test_remind_успешное_создание(self) -> None:
        """Корректный ввод → reminder создан, ответ с ID."""
        from src.handlers.command_handlers import handle_remind

        bot = _make_bot(command_args="10m | купить хлеб")
        msg = _make_message("!remind 10m | купить хлеб")

        mock_cfg = MagicMock()
        mock_cfg.SCHEDULER_ENABLED = True

        mock_scheduler = MagicMock()
        mock_scheduler.is_started = True
        mock_scheduler.add_reminder = MagicMock(return_value="rem-001")

        with (
            patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
            patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler),
        ):
            await handle_remind(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "rem-001" in reply_text
        assert "купить хлеб" in reply_text


# ---------------------------------------------------------------------------
# handle_reminders
# ---------------------------------------------------------------------------


class TestHandleReminders:
    """Тесты отображения списка напоминаний."""

    @pytest.mark.asyncio
    async def test_reminders_пустой_список(self) -> None:
        """Нет напоминаний → сообщение 'нет'."""
        from src.handlers.command_handlers import handle_reminders

        bot = _make_bot()
        msg = _make_message()

        mock_scheduler = MagicMock()
        mock_scheduler.list_reminders = MagicMock(return_value=[])

        with patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler):
            await handle_reminders(bot, msg)

        msg.reply.assert_called_once()
        assert "нет" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_reminders_с_данными(self) -> None:
        """Есть напоминания → выводятся ID, дата, текст."""
        from src.handlers.command_handlers import handle_reminders

        bot = _make_bot()
        msg = _make_message()

        items = [
            {"reminder_id": "rem-42", "due_at_iso": "2026-04-12T18:00:00", "text": "созвон"},
        ]
        mock_scheduler = MagicMock()
        mock_scheduler.list_reminders = MagicMock(return_value=items)

        with patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler):
            await handle_reminders(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "rem-42" in reply_text
        assert "созвон" in reply_text


# ---------------------------------------------------------------------------
# handle_cronstatus
# ---------------------------------------------------------------------------


class TestHandleCronstatus:
    """Тесты отображения статуса scheduler через !cronstatus."""

    @pytest.mark.asyncio
    async def test_cronstatus_отображает_все_поля(self) -> None:
        """Статус содержит started, pending_count, next_due_at."""
        from src.handlers.command_handlers import handle_cronstatus

        bot = _make_bot()
        msg = _make_message()

        status_data = {
            "scheduler_enabled": True,
            "started": True,
            "pending_count": 3,
            "next_due_at": "2026-04-12T18:00:00",
            "storage_path": "/tmp/reminders.json",
        }
        mock_scheduler = MagicMock()
        mock_scheduler.get_status = MagicMock(return_value=status_data)

        with patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler):
            await handle_cronstatus(bot, msg)

        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "True" in reply_text
        assert "3" in reply_text
        assert "18:00:00" in reply_text

    @pytest.mark.asyncio
    async def test_cronstatus_без_next_due_at(self) -> None:
        """Если next_due_at=None — отображается прочерк '-'."""
        from src.handlers.command_handlers import handle_cronstatus

        bot = _make_bot()
        msg = _make_message()

        status_data = {
            "scheduler_enabled": False,
            "started": False,
            "pending_count": 0,
            "next_due_at": None,
            "storage_path": "/tmp/reminders.json",
        }
        mock_scheduler = MagicMock()
        mock_scheduler.get_status = MagicMock(return_value=status_data)

        with patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler):
            await handle_cronstatus(bot, msg)

        reply_text = msg.reply.call_args[0][0]
        assert "- -" in reply_text or ": `-`" in reply_text
