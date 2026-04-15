# -*- coding: utf-8 -*-
"""
Тесты команды !health — глубокая диагностика подсистем Краба.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import handle_health

# Пути для патча локальных импортов внутри handle_health
_SWARM_BUS = "src.core.swarm_bus.TEAM_REGISTRY"
_SWARM_SCHED = "src.core.swarm_scheduler.swarm_scheduler"
_RATE_LIMITER = "src.core.telegram_rate_limiter.telegram_rate_limiter"


def _make_bot(*, me_id: int = 777, proactive_task_done: bool = False) -> SimpleNamespace:
    """Создаёт минимальный stub KraabUserbot для тестов."""
    pw_task = MagicMock()
    pw_task.done.return_value = proactive_task_done

    return SimpleNamespace(
        me=SimpleNamespace(id=me_id),
        _proactive_watch_task=pw_task,
        get_voice_runtime_profile=lambda: {"enabled": True, "voice": "ru-RU-DmitryNeural"},
    )


def _make_message(*, user_id: int = 42) -> SimpleNamespace:
    """Создаёт stub Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _default_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Применяет стандартный набор monkeypatch для нейтральных условий."""
    import src.handlers.command_handlers as mod
    monkeypatch.setattr(mod.openclaw_client, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mod.openclaw_client, "get_last_runtime_route", lambda: {"model": "gemini-test"}
    )
    monkeypatch.setattr(mod, "is_lm_studio_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mod.inbox_service, "get_summary", lambda: {"attention_items": 0, "open_items": 0}
    )
    monkeypatch.setattr(mod.config, "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(mod.config, "LM_STUDIO_URL", "http://localhost:1234", raising=False)
    monkeypatch.setattr(mod.config, "VOICE_REPLY_VOICE", "ru-RU-DmitryNeural", raising=False)


@pytest.mark.asyncio
async def test_handle_health_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: все подсистемы в норме — отчёт без ❌."""
    _default_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = [1, 2]
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 3, "max_per_sec": 20}
    fake_registry = {"traders": [], "coders": []}

    with (
        patch(_SWARM_BUS, fake_registry),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    message.reply.assert_awaited_once()
    report: str = message.reply.call_args[0][0]

    assert "🏥" in report
    assert "❌" not in report, f"Не ожидали ошибок:\n{report}"
    assert "✅ Telegram: connected" in report
    assert "✅ OpenClaw: up" in report
    assert "gemini-test" in report
    assert "✅ LM Studio: online" in report
    assert "✅ Rate Limiter: 3/20 rps" in report


@pytest.mark.asyncio
async def test_handle_health_openclaw_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: OpenClaw offline → строка содержит ❌ OpenClaw."""
    import src.handlers.command_handlers as mod
    _default_patches(monkeypatch)
    monkeypatch.setattr(mod.openclaw_client, "health_check", AsyncMock(return_value=False))
    monkeypatch.setattr(mod.openclaw_client, "get_last_runtime_route", lambda: {})
    monkeypatch.setattr(mod, "is_lm_studio_available", AsyncMock(return_value=False))
    monkeypatch.setattr(mod.config, "SCHEDULER_ENABLED", False, raising=False)

    bot = _make_bot()
    message = _make_message()

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = []
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 0, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    assert "❌ OpenClaw: offline" in report
    assert "❌ LM Studio: offline" in report
    assert "⚠️ Scheduler: disabled" in report


@pytest.mark.asyncio
async def test_handle_health_inbox_attention(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: inbox с attention items → строка ⚠️ Inbox."""
    import src.handlers.command_handlers as mod
    _default_patches(monkeypatch)
    monkeypatch.setattr(
        mod.inbox_service, "get_summary", lambda: {"attention_items": 3, "open_items": 5}
    )

    bot = _make_bot()
    message = _make_message()
    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = [1]
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 1, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {"traders": [], "coders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    assert "⚠️ Inbox: 3 attention items (5 open)" in report


@pytest.mark.asyncio
async def test_handle_health_proactive_watch_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """!health: proactive watch task done → ⚠️ Proactive Watch."""
    _default_patches(monkeypatch)
    bot = _make_bot(proactive_task_done=True)
    message = _make_message()

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = []
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 0, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    assert "⚠️ Proactive Watch: не запущен" in report


@pytest.mark.asyncio
async def test_handle_health_rate_limiter_overload(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: rate limiter перегружен → ⚠️ Rate Limiter."""
    _default_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = []
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 20, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    assert "⚠️ Rate Limiter" in report
    assert "перегрузка" in report


@pytest.mark.asyncio
async def test_handle_health_edit_own_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: если отправитель — сам бот, используется edit вместо reply."""
    _default_patches(monkeypatch)
    bot_id = 999
    bot = _make_bot(me_id=bot_id)
    # Пользователь == сам бот
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=bot_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = []
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 0, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    message.edit.assert_awaited_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_health_swarm_no_teams(monkeypatch: pytest.MonkeyPatch) -> None:
    """!health: пустой TEAM_REGISTRY → ❌ Swarm."""
    _default_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()

    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = []
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 0, "max_per_sec": 20}

    with (
        patch(_SWARM_BUS, {}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    assert "❌ Swarm: команды не зарегистрированы" in report
