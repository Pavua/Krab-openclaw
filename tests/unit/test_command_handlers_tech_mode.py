# -*- coding: utf-8 -*-
"""
Тесты owner-команды `!tech`.

Проверяем:
1) статус честно показывает текущие debug-флаги;
2) owner может включить verbose-режим одной командой;
3) не-owner не получает доступ к debug toggle.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_tech


def _make_bot(*, level: AccessLevel) -> SimpleNamespace:
    return SimpleNamespace(
        _get_access_profile=lambda _user: AccessProfile(level=level, source="test"),
    )


@pytest.mark.asyncio
async def test_handle_tech_status_reports_current_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!tech status` должен показывать truthful owner/debug режим."""
    message = SimpleNamespace(
        text="!tech status",
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    bot = _make_bot(level=AccessLevel.OWNER)
    monkeypatch.setattr(command_handlers_module.config, "USERBOT_TECH_NOTICES_ENABLED", True, raising=False)
    monkeypatch.setattr(
        command_handlers_module.config,
        "USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS",
        False,
        raising=False,
    )

    await handle_tech(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "`verbose`" in text
    assert "`True`" in text


@pytest.mark.asyncio
async def test_handle_tech_verbose_updates_runtime_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!tech verbose` должен включать notices и отключать suppression шумных хвостов."""
    message = SimpleNamespace(
        text="!tech verbose",
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    bot = _make_bot(level=AccessLevel.OWNER)
    applied: list[tuple[str, str]] = []

    def _fake_update(key: str, value: str) -> bool:
        applied.append((key, value))
        return True

    monkeypatch.setattr(command_handlers_module.config, "update_setting", _fake_update, raising=False)

    await handle_tech(bot, message)

    assert ("USERBOT_TECH_NOTICES_ENABLED", "1") in applied
    assert ("USERBOT_SUPPRESS_NON_ACTIONABLE_TOOL_WARNINGS", "0") in applied
    message.reply.assert_awaited_once()
    assert "Verbose debug включён" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_tech_rejects_non_owner() -> None:
    """Команда debug-notices не должна быть доступна не-owner контуру."""
    message = SimpleNamespace(
        text="!tech on",
        from_user=SimpleNamespace(id=2, username="guest"),
        reply=AsyncMock(),
    )
    bot = _make_bot(level=AccessLevel.FULL)

    with pytest.raises(UserInputError) as exc_info:
        await handle_tech(bot, message)
    assert "только владельцу" in str(exc_info.value.user_message or "").lower()
