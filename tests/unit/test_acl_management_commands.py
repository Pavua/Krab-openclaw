# -*- coding: utf-8 -*-
"""
Тесты owner-only управления ACL через Telegram-команду.

Покрываем:
1) owner может смотреть runtime ACL;
2) owner может выдавать partial/full доступ;
3) не-owner не может управлять ACL.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
import src.handlers.commands.admin_commands as admin_commands_module
from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_acl


def _make_bot(args: str, *, access_level: AccessLevel) -> SimpleNamespace:
    return SimpleNamespace(
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(level=access_level, source="test"),
    )


@pytest.mark.asyncio
async def test_handle_acl_status_renders_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    bot = _make_bot("status", access_level=AccessLevel.OWNER)
    monkeypatch.setattr(
        admin_commands_module,
        "load_acl_runtime_state",
        lambda: {"owner": [], "full": ["alpha"], "partial": ["beta"]},
    )

    await handle_acl(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Runtime ACL userbot" in text
    assert "alpha" in text
    assert "beta" in text


@pytest.mark.asyncio
async def test_handle_acl_grant_updates_runtime_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    bot = _make_bot("grant partial @reader", access_level=AccessLevel.OWNER)
    monkeypatch.setattr(
        admin_commands_module,
        "update_acl_subject",
        lambda level, subject, add: {
            "changed": True,
            "subject": "reader",
            "state": {"owner": [], "full": [], "partial": ["reader"]},
        },
    )

    await handle_acl(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "ACL обновлён" in text
    assert "`partial`" in text
    assert "`reader`" in text


@pytest.mark.asyncio
async def test_handle_acl_rejects_non_owner() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=2, username="guest"),
        reply=AsyncMock(),
    )
    bot = _make_bot("grant full @reader", access_level=AccessLevel.FULL)

    with pytest.raises(UserInputError) as exc_info:
        await handle_acl(bot, message)
    assert "только владельцу" in str(exc_info.value.user_message or "").lower()
