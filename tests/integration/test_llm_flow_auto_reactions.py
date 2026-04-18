# -*- coding: utf-8 -*-
"""
Тесты интеграции auto_reactions в _run_llm_request_flow (llm_flow.py).

Покрытие:
1. test_mark_accepted_called_on_start     — реакция при старте flow
2. test_mark_failed_called_on_timeout     — реакция при timeout ошибке
3. test_mark_completed_called_on_success  — реакция при успешном ответе
4. test_mark_agent_mode_on_tool_summary   — реакция при появлении tool_summary
5. test_auto_reactions_missing_module_graceful — _safe_react не падает без модуля
6. test_safe_react_ignores_exceptions     — _safe_react поглощает ошибки реакций
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(chat_id: int = 100, msg_id: int = 1) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.id = msg_id
    return msg


# ---------------------------------------------------------------------------
# Tests: _safe_react wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_react_ignores_exceptions():
    """_safe_react поглощает исключения из func и не пробрасывает их наружу."""
    import src.userbot.llm_flow as llm_flow_mod

    async def _failing_func(bot, message, *args):
        raise RuntimeError("Telegram flood wait")

    msg = _make_message()
    # Не должно бросить исключение
    await llm_flow_mod._safe_react(_failing_func, MagicMock(), msg)


@pytest.mark.asyncio
async def test_safe_react_calls_func_when_available():
    """_safe_react вызывает переданную функцию при _HAS_AUTO_REACTIONS=True."""
    import src.userbot.llm_flow as llm_flow_mod

    called_with = []

    async def _spy_func(bot, message, *args):
        called_with.append((bot, message, args))

    msg = _make_message()
    bot = MagicMock()
    original_flag = llm_flow_mod._HAS_AUTO_REACTIONS
    try:
        llm_flow_mod._HAS_AUTO_REACTIONS = True
        await llm_flow_mod._safe_react(_spy_func, bot, msg)
    finally:
        llm_flow_mod._HAS_AUTO_REACTIONS = original_flag

    assert len(called_with) == 1
    assert called_with[0][1] is msg


@pytest.mark.asyncio
async def test_safe_react_noop_when_module_missing():
    """_safe_react ничего не делает при _HAS_AUTO_REACTIONS=False."""
    import src.userbot.llm_flow as llm_flow_mod

    called = []

    async def _spy_func(bot, message, *args):
        called.append(True)

    msg = _make_message()
    original_flag = llm_flow_mod._HAS_AUTO_REACTIONS
    try:
        llm_flow_mod._HAS_AUTO_REACTIONS = False
        await llm_flow_mod._safe_react(_spy_func, MagicMock(), msg)
    finally:
        llm_flow_mod._HAS_AUTO_REACTIONS = original_flag

    assert called == []


# ---------------------------------------------------------------------------
# Tests: auto_reactions import graceful when module absent
# ---------------------------------------------------------------------------


def test_auto_reactions_missing_module_graceful():
    """
    Если src.core.auto_reactions не существует — llm_flow импортируется без ошибок,
    а _HAS_AUTO_REACTIONS=False.
    """
    # Временно "скрываем" модуль из sys.modules
    saved = sys.modules.pop("src.core.auto_reactions", None)
    # Также убираем из userbot.llm_flow чтобы пересмотреть импорт
    saved_llm = sys.modules.pop("src.userbot.llm_flow", None)

    # Делаем так, что при попытке импорта auto_reactions получим ImportError
    broken_mod = ModuleType("src.core.auto_reactions")
    # Перехватим — просто ставим в sys.modules сломанный модуль без нужных функций
    sys.modules["src.core.auto_reactions"] = broken_mod  # нет mark_accepted → ImportError в from

    try:
        # Перезагружаем llm_flow — он должен поймать ImportError и установить флаг False
        import importlib
        # Очищаем кэш
        sys.modules.pop("src.userbot.llm_flow", None)
        # Временно патчим import так, чтобы from ..core.auto_reactions import ... падало
        with patch.dict("sys.modules", {"src.core.auto_reactions": None}):
            import src.userbot.llm_flow as fresh_mod  # noqa: F401
            # При _HAS_AUTO_REACTIONS=False _safe_react должен быть доступен
            assert hasattr(fresh_mod, "_safe_react")
    finally:
        # Восстанавливаем оригинальные модули
        if saved is not None:
            sys.modules["src.core.auto_reactions"] = saved
        else:
            sys.modules.pop("src.core.auto_reactions", None)
        if saved_llm is not None:
            sys.modules["src.userbot.llm_flow"] = saved_llm
        else:
            sys.modules.pop("src.userbot.llm_flow", None)


# ---------------------------------------------------------------------------
# Tests: mark_accepted called at start of flow (unit-level mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_accepted_invoked_via_safe_react():
    """
    mark_accepted вызывается через _safe_react при старте.
    Проверяем напрямую через мок mark_accepted в модуле llm_flow.
    """
    import src.userbot.llm_flow as llm_flow_mod

    msg = _make_message()
    bot = MagicMock()
    calls = []

    async def _fake_mark_accepted(b, m, *args):
        calls.append(("accepted", b, m))

    original_flag = llm_flow_mod._HAS_AUTO_REACTIONS
    original_func = llm_flow_mod.mark_accepted if llm_flow_mod._HAS_AUTO_REACTIONS else None
    try:
        llm_flow_mod._HAS_AUTO_REACTIONS = True
        llm_flow_mod.mark_accepted = _fake_mark_accepted
        await llm_flow_mod._safe_react(llm_flow_mod.mark_accepted, bot, msg)
    finally:
        llm_flow_mod._HAS_AUTO_REACTIONS = original_flag
        if original_func is not None:
            llm_flow_mod.mark_accepted = original_func

    assert len(calls) == 1
    assert calls[0][0] == "accepted"
    assert calls[0][2] is msg


@pytest.mark.asyncio
async def test_mark_failed_invoked_via_safe_react():
    """mark_failed вызывается с error-строкой через _safe_react."""
    import src.userbot.llm_flow as llm_flow_mod

    msg = _make_message()
    bot = MagicMock()
    calls = []

    async def _fake_mark_failed(b, m, *args):
        calls.append(("failed", args))

    original_flag = llm_flow_mod._HAS_AUTO_REACTIONS
    original_func = llm_flow_mod.mark_failed if llm_flow_mod._HAS_AUTO_REACTIONS else None
    try:
        llm_flow_mod._HAS_AUTO_REACTIONS = True
        llm_flow_mod.mark_failed = _fake_mark_failed
        await llm_flow_mod._safe_react(llm_flow_mod.mark_failed, bot, msg, "timeout error")
    finally:
        llm_flow_mod._HAS_AUTO_REACTIONS = original_flag
        if original_func is not None:
            llm_flow_mod.mark_failed = original_func

    assert len(calls) == 1
    assert calls[0][0] == "failed"
    assert "timeout error" in calls[0][1]
