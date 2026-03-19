# -*- coding: utf-8 -*-
"""
Тесты truthful `!status` обработчика.

Проверяем, что команда показывает фактический runtime-route, а не stale config.MODEL.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.handlers.command_handlers import handle_status


@pytest.mark.asyncio
async def test_handle_status_prefers_runtime_route_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!status` должен выводить фактическую модель и канал маршрута."""
    bot = SimpleNamespace(
        current_role="default",
        voice_mode=False,
        me=SimpleNamespace(id=777),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )

    monkeypatch.setattr(
        command_handlers_module.model_manager,
        "get_ram_usage",
        lambda: {"percent": 47.5},
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
            "model": "google-gemini-cli/gemini-3-flash-preview",
            "channel": "openclaw_cloud",
            "status": "ok",
        },
    )
    monkeypatch.setattr(
        command_handlers_module,
        "get_runtime_primary_model",
        lambda: "google/gemini-3.1-pro-preview",
    )
    monkeypatch.setattr(command_handlers_module.config, "MODEL", "openai-codex/gpt-5.4", raising=False)

    await handle_status(bot, message)

    rendered = message.reply.await_args.args[0]
    assert "Фактическая модель" in rendered
    assert "google-gemini-cli/gemini-3-flash-preview" in rendered
    assert "Primary runtime" in rendered
    assert "google/gemini-3.1-pro-preview" in rendered
    assert "openclaw_cloud" in rendered
