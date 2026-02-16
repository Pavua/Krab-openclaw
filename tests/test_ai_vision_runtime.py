# -*- coding: utf-8 -*-
"""Тесты runtime-команды !vision."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers.ai import register_handlers


class _MockMessage:
    def __init__(self, text: str):
        self.text = text
        self.command = text.split()
        self.chat = SimpleNamespace(id=-100123, type=SimpleNamespace(name="PRIVATE"))
        self.from_user = SimpleNamespace(username="owner", id=1, is_self=True)
        self.reply_text = AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock()))


def _build_handlers() -> tuple[dict, dict]:
    handlers: dict[str, callable] = {}
    app = MagicMock()

    def on_message(*args, **kwargs):
        def decorator(func):
            handlers[func.__name__] = func
            return func

        return decorator

    def on_raw_update(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    app.on_message = on_message
    app.on_raw_update = on_raw_update

    router = MagicMock()
    router.route_query = AsyncMock(return_value="ok")
    router.classify_task_profile = MagicMock(return_value="chat")
    router.require_confirm_expensive = False
    router.active_local_model = "vision-live-model"
    router.local_preferred_model = "vision-preferred"

    perceptor = MagicMock()
    perceptor.local_vision_enabled = False
    perceptor.local_vision_model = ""
    perceptor.local_vision_timeout_seconds = 90
    perceptor.local_vision_max_tokens = 1200
    perceptor.vision_model = "gemini-2.0-flash"
    perceptor._resolve_local_vision_model = MagicMock(return_value="vision-live-model")

    config_manager = MagicMock()

    deps = {
        "router": router,
        "memory": MagicMock(get_recent_context=MagicMock(return_value=[]), save_message=MagicMock()),
        "security": MagicMock(can_execute_command=MagicMock(return_value=True)),
        "agent": MagicMock(solve_complex_task=AsyncMock(return_value="agent-ok")),
        "rate_limiter": MagicMock(),
        "safe_handler": lambda f: f,
        "tools": MagicMock(),
        "perceptor": perceptor,
        "config_manager": config_manager,
    }
    register_handlers(app, deps)
    return handlers, deps


@pytest.mark.asyncio
async def test_vision_local_on_updates_runtime_and_config() -> None:
    handlers, deps = _build_handlers()
    msg = _MockMessage("!vision local on")
    await handlers["vision_command"](None, msg)
    assert deps["perceptor"].local_vision_enabled is True
    deps["config_manager"].set.assert_any_call("LOCAL_VISION_ENABLED", "1")


@pytest.mark.asyncio
async def test_vision_model_updates_runtime_and_config() -> None:
    handlers, deps = _build_handlers()
    msg = _MockMessage("!vision model zai-org/glm-4.6v-flash")
    await handlers["vision_command"](None, msg)
    assert deps["perceptor"].local_vision_model == "zai-org/glm-4.6v-flash"
    deps["config_manager"].set.assert_any_call("LOCAL_VISION_MODEL", "zai-org/glm-4.6v-flash")


@pytest.mark.asyncio
async def test_vision_status_returns_snapshot() -> None:
    handlers, _ = _build_handlers()
    msg = _MockMessage("!vision status")
    await handlers["vision_command"](None, msg)
    assert msg.reply_text.await_count >= 1
    text = msg.reply_text.await_args_list[-1].args[0]
    assert "Vision Runtime" in text
    assert "Local vision" in text
