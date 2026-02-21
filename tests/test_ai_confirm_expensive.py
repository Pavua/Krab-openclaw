# -*- coding: utf-8 -*-
"""Тесты confirm-step для AI команд Telegram."""

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
        self._notification = SimpleNamespace(edit_text=AsyncMock())
        self.reply_text = AsyncMock(return_value=self._notification)


def _build_handlers(router: MagicMock | None = None, agent: MagicMock | None = None) -> tuple[dict, dict]:
    handlers: dict[str, callable] = {}
    app = MagicMock()

    def on_message(*args, **kwargs):
        def decorator(func):
            handlers[func.__name__] = func
            return func

        return decorator

    app.on_message = on_message
    router = router or MagicMock()
    router.route_query = AsyncMock(return_value="ok")
    router.classify_task_profile = MagicMock(return_value="chat")
    if not hasattr(router, "require_confirm_expensive"):
        router.require_confirm_expensive = False

    deps = {
        "router": router,
        "memory": MagicMock(get_recent_context=MagicMock(return_value=[]), save_message=MagicMock()),
        "security": MagicMock(can_execute_command=MagicMock(return_value=True)),
        "agent": agent or MagicMock(solve_complex_task=AsyncMock(return_value="agent-ok")),
        "rate_limiter": MagicMock(),
        "safe_handler": lambda f: f,
        "tools": MagicMock(),
    }
    register_handlers(app, deps)
    return handlers, deps


@pytest.mark.asyncio
async def test_think_passes_confirm_expensive_flag() -> None:
    handlers, deps = _build_handlers()
    msg = _MockMessage("!think --confirm-expensive Проверь архитектуру безопасности")
    await handlers["think_command"](None, msg)
    kwargs = deps["router"].route_query.await_args.kwargs
    assert kwargs["confirm_expensive"] is True
    assert kwargs["task_type"] == "reasoning"


@pytest.mark.asyncio
async def test_code_without_confirm_passes_false() -> None:
    handlers, deps = _build_handlers()
    msg = _MockMessage("!code Напиши health endpoint")
    await handlers["code_command"](None, msg)
    kwargs = deps["router"].route_query.await_args.kwargs
    assert kwargs["confirm_expensive"] is False
    assert kwargs["task_type"] == "coding"


@pytest.mark.asyncio
async def test_smart_blocks_critical_without_confirm_when_required() -> None:
    router = MagicMock()
    agent = MagicMock(solve_complex_task=AsyncMock(return_value="agent-ok"))
    handlers, deps = _build_handlers(router=router, agent=agent)
    deps["router"].classify_task_profile = MagicMock(return_value="security")
    deps["router"].require_confirm_expensive = True

    msg = _MockMessage("!smart Проведи security аудит прода")
    await handlers["smart_command"](None, msg)

    agent.solve_complex_task.assert_not_awaited()
    # Последний ответ должен содержать подсказку confirm-step.
    assert "--confirm-expensive" in msg.reply_text.await_args_list[-1].args[0]
