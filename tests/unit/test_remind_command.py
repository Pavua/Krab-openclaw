# -*- coding: utf-8 -*-
"""
Tests for handle_remind — event-based branch via reminders_queue (Wave 7-D).

Use mocks for reminders_queue singleton.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_message(text: str = "", chat_id: int = 100, user_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()
    return msg


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


# ---------------------------------------------------------------------------
# Event-based reminder creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remind_event_when_creates_event_reminder() -> None:
    """'!remind when upload photos then notify me' → event reminder."""
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="when upload photos then notify me")
    msg = _make_message(
        "!remind when upload photos then notify me",
        chat_id=555,
        user_id=42,
    )

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_rq = MagicMock()
    mock_rq.add_event_reminder = MagicMock(return_value="evt-abc")

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    mock_rq.add_event_reminder.assert_called_once()
    call_kwargs = mock_rq.add_event_reminder.call_args.kwargs
    assert call_kwargs["owner_id"] == "42"
    assert call_kwargs["chat_id"] == "555"
    assert call_kwargs["pattern"] == "upload photos"
    assert call_kwargs["action"] == "notify me"
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "evt-abc" in reply_text
    assert "upload photos" in reply_text


@pytest.mark.asyncio
async def test_remind_event_russian_когда_сделай() -> None:
    """'!remind когда upload сделай X' → event reminder."""
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="когда upload сделай экспорт")
    msg = _make_message("!remind когда upload сделай экспорт", chat_id=777)

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_rq = MagicMock()
    mock_rq.add_event_reminder = MagicMock(return_value="evt-ru")

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    mock_rq.add_event_reminder.assert_called_once()
    call_kwargs = mock_rq.add_event_reminder.call_args.kwargs
    assert call_kwargs["pattern"] == "upload"
    assert call_kwargs["action"] == "экспорт"
    assert "evt-ru" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Cancel via reminders_queue fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remind_cancel_falls_back_to_reminders_queue() -> None:
    """Если scheduler.remove вернул False — cancel пробуется через reminders_queue."""
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="cancel evt-xyz")
    msg = _make_message("!remind cancel evt-xyz")

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_scheduler = MagicMock()
    mock_scheduler.remove_reminder = MagicMock(return_value=False)

    mock_rq = MagicMock()
    mock_rq.cancel = MagicMock(return_value=True)

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    mock_scheduler.remove_reminder.assert_called_once_with("evt-xyz")
    mock_rq.cancel.assert_called_once_with("evt-xyz")
    msg.reply.assert_called_once()
    assert "отменено" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_remind_cancel_not_found_anywhere() -> None:
    """Если не найдено ни в scheduler, ни в queue → сообщение 'не найдено'."""
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="cancel missing")
    msg = _make_message("!remind cancel missing")

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_scheduler = MagicMock()
    mock_scheduler.remove_reminder = MagicMock(return_value=False)

    mock_rq = MagicMock()
    mock_rq.cancel = MagicMock(return_value=False)

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    msg.reply.assert_called_once()
    assert "не найдено" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# List includes both scheduler and reminders_queue entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remind_list_empty_both_scheduler_and_queue() -> None:
    """Пустой scheduler + пустой queue → 'нет напоминаний'."""
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="list")
    msg = _make_message("!remind list", user_id=42)

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_scheduler = MagicMock()
    mock_scheduler.list_reminders = MagicMock(return_value=[])

    mock_rq = MagicMock()
    mock_rq.list_pending = MagicMock(return_value=[])

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    msg.reply.assert_called_once()
    assert "нет" in msg.reply.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_remind_list_includes_event_entries() -> None:
    """Event-based записи из reminders_queue попадают в list."""
    from src.core.reminders_queue import (
        Reminder,
        ReminderStatus,
        ReminderTrigger,
    )
    from src.handlers.command_handlers import handle_remind

    bot = _make_bot(command_args="list")
    msg = _make_message("!remind list", user_id=42)

    mock_cfg = MagicMock()
    mock_cfg.SCHEDULER_ENABLED = True

    mock_scheduler = MagicMock()
    mock_scheduler.list_reminders = MagicMock(return_value=[])

    event_reminder = Reminder(
        id="evt-9",
        owner_user_id="42",
        created_at=1700000000,
        trigger_type=ReminderTrigger.EVENT,
        watch_chat_id="100",
        match_pattern="upload photos",
        action_payload="notify me",
        status=ReminderStatus.PENDING,
    )
    mock_rq = MagicMock()
    mock_rq.list_pending = MagicMock(return_value=[event_reminder])

    with (
        patch("src.handlers.commands.scheduler_commands.config", mock_cfg),
        patch("src.handlers.commands.scheduler_commands.krab_scheduler", mock_scheduler),
        patch("src.core.reminders_queue.reminders_queue", mock_rq),
    ):
        await handle_remind(bot, msg)

    msg.reply.assert_called_once()
    text = msg.reply.call_args[0][0]
    assert "evt-9" in text
    assert "upload photos" in text
    assert "notify me" in text
