# -*- coding: utf-8 -*-
"""
Тесты truthful-статуса для Telegram-команды `!status`.

Проверяем, что:
1) ответ берёт фактическую live-модель из последнего runtime route;
2) configured primary показывается отдельно и не теряется;
3) если live route отличается от configured primary, пользователь видит честную пометку.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.handlers.command_handlers import handle_status


@pytest.mark.asyncio
async def test_handle_status_uses_live_route_truth_over_stale_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`!status` должен показывать live route, а не застывший config.MODEL."""
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    bot = SimpleNamespace(
        current_role="default",
        voice_mode=False,
        me=SimpleNamespace(id=999, username="krab"),
    )

    monkeypatch.setattr(
        command_handlers_module.model_manager,
        "get_ram_usage",
        lambda: {"percent": 42},
    )
    monkeypatch.setattr(
        command_handlers_module.openclaw_client,
        "health_check",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        command_handlers_module.openclaw_client,
        "get_last_runtime_route",
        lambda: {
            "model": "google/gemini-3.1-pro-preview",
            "provider": "google",
            "channel": "openclaw_cloud",
            "status": "ok",
        },
    )
    monkeypatch.setattr(
        command_handlers_module,
        "get_runtime_primary_model",
        lambda: "google-gemini-cli/gemini-3-flash-preview",
    )
    monkeypatch.setattr(command_handlers_module.config, "MODEL", "openai-codex/gpt-4.5", raising=False)

    await handle_status(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "`google/gemini-3.1-pro-preview`" in text
    assert "`google-gemini-cli/gemini-3-flash-preview`" in text
    assert "`google`" in text
    assert "`openclaw_cloud`" in text
    assert "последний успешный route сейчас отличается от configured primary" in text
