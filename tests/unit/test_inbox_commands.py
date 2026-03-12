# -*- coding: utf-8 -*-
"""
Тесты owner-visible inbox-команды.

Покрываем:
1) `!inbox status` показывает summary;
2) `!inbox done <id>` обновляет статус persisted item;
3) `!inbox approval ...` создаёт approval-request;
4) `!inbox approve <id>` принимает approval-request;
5) неизвестный id даёт понятную ошибку.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.core.exceptions import UserInputError
from src.core.inbox_service import InboxService
from src.handlers.command_handlers import handle_inbox


def _make_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        reply=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_handle_inbox_status_renders_summary(tmp_path) -> None:
    """`!inbox status` должен печатать summary persisted inbox."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    inbox.upsert_reminder(
        reminder_id="abc123",
        chat_id="-10077",
        text="проверить контракт",
        due_at_iso="2026-03-12T10:00:00+00:00",
    )
    message = _make_message("!inbox status")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Inbox / Escalation" in text
    assert "pending_reminders" in text


@pytest.mark.asyncio
async def test_handle_inbox_done_updates_item(tmp_path) -> None:
    """`!inbox done <id>` должен закрывать item."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    item = inbox.upsert_item(
        dedupe_key="watch:gateway_down",
        kind="watch_alert",
        source="proactive-watch",
        title="Gateway недоступен",
        body="gateway down",
        severity="error",
    )["item"]
    message = _make_message(f"!inbox done {item['item_id']}")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    message.reply.assert_awaited_once()
    assert inbox.list_items(status="done", kind="watch_alert", limit=5)


@pytest.mark.asyncio
async def test_handle_inbox_done_rejects_unknown_item(tmp_path) -> None:
    """Неизвестный id должен давать понятную ошибку."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    message = _make_message("!inbox done missing-id")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        with pytest.raises(UserInputError) as exc_info:
            await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    assert "не найден" in str(exc_info.value.user_message or "").lower()


@pytest.mark.asyncio
async def test_handle_inbox_approval_creates_request(tmp_path) -> None:
    """`!inbox approval ...` должен создавать approval-request."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    message = _make_message("!inbox approval money | Разрешить платный API | Нужен budget approval")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    message.reply.assert_awaited_once()
    items = inbox.list_items(status="open", kind="approval_request", limit=5)
    assert items
    assert items[0]["identity"]["approval_scope"] == "money"


@pytest.mark.asyncio
async def test_handle_inbox_approve_updates_approval_request(tmp_path) -> None:
    """`!inbox approve <id>` должен переводить approval-request в approved."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    item = inbox.upsert_approval_request(
        title="Включить платный cloud route",
        body="Нужен production smoke.",
        request_key="paid-cloud",
        approval_scope="money",
    )["item"]
    message = _make_message(f"!inbox approve {item['item_id']}")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    message.reply.assert_awaited_once()
    approved_items = inbox.list_items(status="approved", kind="approval_request", limit=5)
    assert approved_items


@pytest.mark.asyncio
async def test_handle_inbox_approve_rejects_non_approval_item(tmp_path) -> None:
    """`!inbox approve` должен отклонять обычный owner-task."""
    inbox = InboxService(state_path=tmp_path / "inbox.json")
    item = inbox.upsert_owner_task(
        title="Проверить reserve bot",
        body="Нужен smoke.",
        task_key="reserve-bot-smoke",
    )["item"]
    message = _make_message(f"!inbox approve {item['item_id']}")
    bot = SimpleNamespace()
    original = command_handlers_module.inbox_service
    command_handlers_module.inbox_service = inbox
    try:
        with pytest.raises(UserInputError) as exc_info:
            await handle_inbox(bot, message)
    finally:
        command_handlers_module.inbox_service = original

    assert "approval-request" in str(exc_info.value.user_message or "").lower()
