# -*- coding: utf-8 -*-
"""
Wave 30-C: Тесты bypass providers секции в !health.

Проверяем, что:
- CLI subprocess / Google Vertex / Anthropic Vertex / Google AI Studio статус
  присутствует в отчёте
- Bypass calls today строка есть
- Codex accounts секция появляется когда есть аккаунты
- Ошибки в bypass функциях не ронят весь health-check
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import handle_health

# Патч-пути для базовых зависимостей (те же, что в test_handle_health_command.py)
_SWARM_BUS = "src.core.swarm_bus.TEAM_REGISTRY"
_SWARM_SCHED = "src.core.swarm_scheduler.swarm_scheduler"
_RATE_LIMITER = "src.core.telegram_rate_limiter.telegram_rate_limiter"

# Патч-пути для bypass-функций (относительно system_commands.py)
_CLI_ENABLED = "src.integrations.cli_subprocess_bypass.is_cli_subprocess_enabled"
_VERTEX_ENABLED = "src.integrations.google_vertex_direct.is_vertex_enabled"
_AV_ENABLED = "src.integrations.anthropic_vertex_direct.is_anthropic_vertex_enabled"
_GD_ENABLED = "src.integrations.google_genai_direct.is_google_direct_enabled"
_COUNT_CALLS = "src.handlers.commands.observability_commands._count_today_calls"
_LIST_ACCOUNTS = "src.integrations.codex_account_rotator.list_accounts"


def _make_bot(*, me_id: int = 777) -> SimpleNamespace:
    """Минимальный stub KraabUserbot."""
    pw_task = MagicMock()
    pw_task.done.return_value = False
    return SimpleNamespace(
        me=SimpleNamespace(id=me_id),
        _proactive_watch_task=pw_task,
        get_voice_runtime_profile=lambda: {"enabled": True, "voice": "ru-RU-DmitryNeural"},
    )


def _make_message(*, user_id: int = 42) -> SimpleNamespace:
    """Stub Message."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _base_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Применяет стандартный набор monkeypatch для базовых подсистем."""
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


def _mock_sched_rl() -> tuple[MagicMock, MagicMock]:
    """Возвращает моки scheduler и rate limiter."""
    mock_sched = MagicMock()
    mock_sched.list_jobs.return_value = [1]
    mock_rl = MagicMock()
    mock_rl.stats.return_value = {"current_in_window": 2, "max_per_sec": 20}
    return mock_sched, mock_rl


@pytest.mark.asyncio
async def test_bypass_section_all_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass section: все провайдеры enabled → все ✅ в отчёте."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    with (
        patch(_SWARM_BUS, {"traders": [], "coders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, return_value=True),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, return_value={"codex": 5, "gemini": 3, "vertex": 1, "anthropic": 0}),
        patch(_LIST_ACCOUNTS, return_value=[]),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]

    # Bypass провайдеры секция присутствует
    assert "Bypass providers" in report
    assert "CLI subprocess" in report
    assert "Google Vertex" in report
    assert "Anthropic Vertex" in report
    assert "Google AI Studio" in report

    # Все enabled → только ✅ в bypass строках
    assert "❌" not in report

    # Счётчики calls today
    assert "codex: 5" in report
    assert "gemini: 3" in report


@pytest.mark.asyncio
async def test_bypass_section_cli_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass section: CLI subprocess disabled → ❌ CLI subprocess."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, return_value=False),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, return_value={"codex": 0, "gemini": 0, "vertex": 0, "anthropic": 0}),
        patch(_LIST_ACCOUNTS, return_value=[]),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    # CLI subprocess disabled — должен показать ❌
    assert "❌" in report
    # Но Vertex и другие ещё ✅ — убеждаемся что не все упали
    assert "✅" in report
    assert "CLI subprocess" in report


@pytest.mark.asyncio
async def test_bypass_section_codex_accounts_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass section: есть Codex аккаунты → отображаются в Codex accounts."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    fake_accounts = [
        {"name": "primary", "logged_in": True, "available": True, "calls_today": 89},
        {"name": "account2", "logged_in": True, "available": False, "calls_today": 12},
    ]

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, return_value=True),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, return_value={"codex": 89, "gemini": 0, "vertex": 0, "anthropic": 0}),
        patch(_LIST_ACCOUNTS, return_value=fake_accounts),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]

    assert "Codex accounts" in report
    assert "primary" in report
    assert "calls: 89" in report
    assert "account2" in report
    # account2 не available → ⏸
    assert "⏸" in report


@pytest.mark.asyncio
async def test_bypass_section_no_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass section: нет Codex аккаунтов → секция Codex accounts не показывается."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, return_value=True),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, return_value={"codex": 0, "gemini": 0, "vertex": 0, "anthropic": 0}),
        patch(_LIST_ACCOUNTS, return_value=[]),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    # Аккаунтов нет — секция Codex accounts не отображается
    assert "Codex accounts" not in report


@pytest.mark.asyncio
async def test_bypass_section_error_resilient(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass section: ошибки в функциях не ронят всю команду !health."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    def _raise() -> bool:
        raise RuntimeError("test error")

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, side_effect=RuntimeError("cli_error")),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, side_effect=RuntimeError("counts_error")),
        patch(_LIST_ACCOUNTS, side_effect=RuntimeError("accounts_error")),
    ):
        await handle_health(bot, message)

    # Команда не упала — reply был вызван
    message.reply.assert_awaited_once()
    report: str = message.reply.call_args[0][0]

    # Заголовок health-check на месте
    assert "🏥" in report
    # Bypass секция тоже присутствует
    assert "Bypass providers" in report
    # Ошибки показаны через ❓
    assert "❓" in report


@pytest.mark.asyncio
async def test_bypass_section_calls_today_zeros(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass calls today: нулевые счётчики корректно отображаются."""
    _base_patches(monkeypatch)
    bot = _make_bot()
    message = _make_message()
    mock_sched, mock_rl = _mock_sched_rl()

    with (
        patch(_SWARM_BUS, {"traders": []}),
        patch(_SWARM_SCHED, mock_sched),
        patch(_RATE_LIMITER, mock_rl),
        patch(_CLI_ENABLED, return_value=True),
        patch(_VERTEX_ENABLED, return_value=True),
        patch(_AV_ENABLED, return_value=True),
        patch(_GD_ENABLED, return_value=True),
        patch(_COUNT_CALLS, return_value={"codex": 0, "gemini": 0, "vertex": 0, "anthropic": 0}),
        patch(_LIST_ACCOUNTS, return_value=[]),
    ):
        await handle_health(bot, message)

    report: str = message.reply.call_args[0][0]
    # Строка calls today присутствует даже при нулях
    assert "Bypass calls today" in report
    assert "codex: 0" in report
