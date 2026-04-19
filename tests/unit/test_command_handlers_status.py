# -*- coding: utf-8 -*-
"""
Тесты компактного !status — обработчик handle_status.

Проверяем формат вывода, корректность данных из каждой подсистемы
и graceful-деградацию при недоступных компонентах.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.handlers.command_handlers as command_handlers_module
from src.handlers.command_handlers import handle_status

# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные фикстуры
# ─────────────────────────────────────────────────────────────────────────────


def _make_bot(**kwargs) -> SimpleNamespace:
    """Минимальный bot stub."""
    defaults = dict(
        current_role="default",
        voice_mode=False,
        me=SimpleNamespace(id=777),
        _session_start_time=time.time() - 3600,  # 1 час назад
        _session_messages_processed=42,
    )
    defaults.update(kwargs)
    bot = SimpleNamespace(**defaults)
    # translator state
    bot.get_translator_session_state = lambda: {"session_status": "idle", "last_pair": ""}
    return bot


def _make_message(from_user_id: int = 99) -> SimpleNamespace:
    """Минимальный message stub."""
    return SimpleNamespace(
        from_user=SimpleNamespace(id=from_user_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _patch_all(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    """Монтирует стандартные заглушки для всех внешних зависимостей."""
    monkeypatch.setattr(
        command_handlers_module.openclaw_client,
        "health_check",
        overrides.get("oc_health", AsyncMock(return_value=True)),
    )
    monkeypatch.setattr(
        command_handlers_module.openclaw_client,
        "get_last_runtime_route",
        overrides.get(
            "route",
            lambda: {
                "model": "google/gemini-3-pro-preview",
                "channel": "openclaw_cloud",
                "status": "ok",
            },
        ),
    )
    monkeypatch.setattr(
        command_handlers_module,
        "get_runtime_primary_model",
        overrides.get("primary_model", lambda: "google/gemini-3-pro-preview"),
    )
    monkeypatch.setattr(
        command_handlers_module.inbox_service,
        "get_summary",
        overrides.get("inbox", lambda: {"open_items": 5, "attention_items": 0}),
    )
    monkeypatch.setattr(
        command_handlers_module.cost_analytics,
        "build_usage_report_dict",
        overrides.get(
            "cost",
            lambda: {"cost_month_usd": 0.12, "monthly_budget_usd": 50.0},
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Тесты формата вывода
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_status_format_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заголовок содержит 🦀 Krab Status и разделитель."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "🦀 **Krab Status**" in text
    assert "━━━━━━━━━━━━" in text


@pytest.mark.asyncio
async def test_handle_status_telegram_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram подключён → ✅ Telegram в первой строке данных."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "✅ Telegram" in text


@pytest.mark.asyncio
async def test_handle_status_telegram_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """me is None → ❌ Telegram, не вызывает AttributeError."""
    bot = _make_bot(me=None)
    # Когда me=None, from_user.id никогда не совпадёт с me.id, выбирается reply()
    msg = _make_message(from_user_id=99)
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "❌ Telegram" in text


@pytest.mark.asyncio
async def test_handle_status_openclaw_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenClaw online → ✅ OpenClaw (model_short) в первой строке."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "✅ OpenClaw" in text
    # Модель сокращена до части после "/"
    assert "gemini-3-pro-preview" in text


@pytest.mark.asyncio
async def test_handle_status_openclaw_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenClaw offline → ❌ OpenClaw."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch, oc_health=AsyncMock(return_value=False))

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "❌ OpenClaw" in text


@pytest.mark.asyncio
async def test_handle_status_model_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модель отображается сокращённо (без провайдера)."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        route=lambda: {"model": "anthropic/claude-opus-4", "channel": "cloud"},
        primary_model=lambda: "anthropic/claude-opus-4",
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "claude-opus-4" in text
    # Провайдер не должен дублироваться в первой строке отдельно
    lines = text.split("\n")
    assert any("claude-opus-4" in l for l in lines)


@pytest.mark.asyncio
async def test_handle_status_scheduler_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Количество jobs из swarm_scheduler.list_jobs() отображается корректно."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    import src.core.swarm_scheduler as ss_module

    mock_scheduler = MagicMock()
    mock_scheduler.list_jobs.return_value = [{"id": "j1"}, {"id": "j2"}, {"id": "j3"}]

    original_sched = ss_module.swarm_scheduler
    try:
        ss_module.swarm_scheduler = mock_scheduler
        await handle_status(bot, msg)
    finally:
        ss_module.swarm_scheduler = original_sched

    text = msg.reply.await_args.args[0]
    assert "3 jobs" in text
    assert "✅ Scheduler" in text


@pytest.mark.asyncio
async def test_handle_status_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inbox показывает количество открытых задач."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch, inbox=lambda: {"open_items": 7, "attention_items": 2})

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Inbox: 7 open" in text


@pytest.mark.asyncio
async def test_handle_status_cost_with_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Бюджет отображается как cost/budget."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        cost=lambda: {"cost_month_usd": 0.12, "monthly_budget_usd": 50.0},
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Cost: $0.12/50.00" in text


@pytest.mark.asyncio
async def test_handle_status_cost_no_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без бюджета — только сумма расходов."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        cost=lambda: {"cost_month_usd": 0.05, "monthly_budget_usd": 0.0},
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Cost: $0.05" in text
    # Не должно быть "/0.00"
    assert "/0.00" not in text


@pytest.mark.asyncio
async def test_handle_status_swarm_teams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Количество swarm-команд отображается корректно."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    import src.core.swarm_bus as sb_module

    original = sb_module.TEAM_REGISTRY
    try:
        sb_module.TEAM_REGISTRY = {"traders": {}, "coders": {}, "analysts": {}, "creative": {}}
        await handle_status(bot, msg)
    finally:
        sb_module.TEAM_REGISTRY = original

    text = msg.reply.await_args.args[0]
    assert "Swarm: 4 teams" in text


@pytest.mark.asyncio
async def test_handle_status_translator_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Translator idle → отображается как idle."""
    bot = _make_bot()
    bot.get_translator_session_state = lambda: {"session_status": "idle", "last_pair": ""}
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Translator: idle" in text


@pytest.mark.asyncio
async def test_handle_status_translator_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Translator active с парой → отображается с парой."""
    bot = _make_bot()
    bot.get_translator_session_state = lambda: {
        "session_status": "active",
        "last_pair": "es→ru",
    }
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Translator: active (es→ru)" in text


@pytest.mark.asyncio
async def test_handle_status_silence_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тишина выключена → Silence: off."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    import src.core.silence_mode as sm_module

    sm_module.silence_manager.status = lambda: {"global_muted": False}
    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Silence: off" in text


@pytest.mark.asyncio
async def test_handle_status_silence_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тишина включена → Silence: on."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    import src.core.silence_mode as sm_module

    original_status = sm_module.silence_manager.status
    sm_module.silence_manager.status = lambda: {"global_muted": True}
    try:
        await handle_status(bot, msg)
    finally:
        sm_module.silence_manager.status = original_status

    text = msg.reply.await_args.args[0]
    assert "Silence: on" in text


@pytest.mark.asyncio
async def test_handle_status_uptime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uptime вычисляется из _session_start_time."""
    start = time.time() - 7500  # 2ч 5м
    bot = _make_bot(_session_start_time=start)
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Uptime:" in text
    assert "2h5m" in text


@pytest.mark.asyncio
async def test_handle_status_uptime_minutes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Менее часа — отображается только минуты."""
    start = time.time() - 900  # 15 минут
    bot = _make_bot(_session_start_time=start)
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "15m" in text
    assert "0h" not in text


@pytest.mark.asyncio
async def test_handle_status_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAM отображается в мегабайтах."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "MB" in text
    assert "RAM:" in text


@pytest.mark.asyncio
async def test_handle_status_message_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Количество обработанных сообщений отображается корректно."""
    bot = _make_bot(_session_messages_processed=99)
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Messages: 99" in text


@pytest.mark.asyncio
async def test_handle_status_primary_runtime_shown_when_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary runtime показывается, если отличается от фактической модели."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        route=lambda: {"model": "google/gemini-3-flash-preview", "channel": "cloud"},
        primary_model=lambda: "google/gemini-3-pro-preview",
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Primary runtime" in text
    assert "gemini-3-pro-preview" in text


@pytest.mark.asyncio
async def test_handle_status_no_primary_runtime_when_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary runtime НЕ показывается, если совпадает с фактической."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        route=lambda: {"model": "google/gemini-3-pro-preview", "channel": "cloud"},
        primary_model=lambda: "google/gemini-3-pro-preview",
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Primary runtime" not in text


@pytest.mark.asyncio
async def test_handle_status_uses_edit_for_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если from_user.id == bot.me.id — использует edit(), не reply()."""
    bot = _make_bot()
    bot.me = SimpleNamespace(id=777)
    msg = _make_message(from_user_id=777)
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    msg.edit.assert_awaited_once()
    msg.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_status_uses_reply_for_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если from_user.id != bot.me.id — использует reply()."""
    bot = _make_bot()
    msg = _make_message(from_user_id=99)
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    msg.reply.assert_awaited_once()
    msg.edit.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# Graceful деградация при исключениях подсистем
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_status_openclaw_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в health_check → ❌ OpenClaw, не падает."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        oc_health=AsyncMock(side_effect=RuntimeError("network error")),
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "❌ OpenClaw" in text


@pytest.mark.asyncio
async def test_handle_status_inbox_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в inbox → 0 open, не падает."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        inbox=lambda: (_ for _ in ()).throw(RuntimeError("db error")),
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Inbox: 0 open" in text


@pytest.mark.asyncio
async def test_handle_status_cost_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в cost_analytics → '?' в строке cost, не падает."""
    bot = _make_bot()
    msg = _make_message()
    _patch_all(
        monkeypatch,
        cost=lambda: (_ for _ in ()).throw(RuntimeError("cost error")),
    )

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Cost: ?" in text


@pytest.mark.asyncio
async def test_handle_status_translator_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в get_translator_session_state → 'idle', не падает."""
    bot = _make_bot()
    bot.get_translator_session_state = lambda: (_ for _ in ()).throw(
        RuntimeError("translator error")
    )
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Translator: idle" in text


@pytest.mark.asyncio
async def test_handle_status_uptime_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет _session_start_time → uptime '?', не падает."""
    bot = _make_bot()
    del bot._session_start_time
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Uptime: ?" in text


@pytest.mark.asyncio
async def test_handle_status_message_count_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет _session_messages_processed → Messages: 0, не падает."""
    bot = _make_bot()
    del bot._session_messages_processed
    msg = _make_message()
    _patch_all(monkeypatch)

    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Messages: 0" in text


@pytest.mark.asyncio
async def test_handle_status_all_subsystems_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Все подсистемы недоступны → команда завершается без исключений."""
    bot = _make_bot(me=None)
    del bot._session_start_time
    del bot._session_messages_processed
    bot.get_translator_session_state = lambda: (_ for _ in ()).throw(RuntimeError())
    # from_user_id=99 чтобы не упасть на bot.me.id при me=None
    msg = _make_message(from_user_id=99)
    _patch_all(
        monkeypatch,
        oc_health=AsyncMock(side_effect=RuntimeError()),
        inbox=lambda: (_ for _ in ()).throw(RuntimeError()),
        cost=lambda: (_ for _ in ()).throw(RuntimeError()),
    )

    # Не должно упасть
    await handle_status(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "🦀 **Krab Status**" in text
    assert "❌ Telegram" in text
    assert "❌ OpenClaw" in text


# ─────────────────────────────────────────────────────────────────────────────
# Тест совместимости с тестом из predыдущего поколения
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_status_prefers_runtime_route_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """!status выводит фактическую модель из runtime-route, а не stale config."""
    bot = _make_bot()
    message = _make_message()

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
    monkeypatch.setattr(
        command_handlers_module.inbox_service,
        "get_summary",
        lambda: {"open_items": 0, "attention_items": 0},
    )
    monkeypatch.setattr(
        command_handlers_module.cost_analytics,
        "build_usage_report_dict",
        lambda: {"cost_month_usd": 0.0, "monthly_budget_usd": 0.0},
    )
    monkeypatch.setattr(
        command_handlers_module.config, "MODEL", "openai-codex/gpt-5.4", raising=False
    )

    await handle_status(bot, message)

    rendered = message.reply.await_args.args[0]
    # Фактическая модель из route — сокращённая
    assert "gemini-3-flash-preview" in rendered
    # Primary runtime показывается, т.к. отличается от фактической
    assert "Primary runtime" in rendered
    assert "gemini-3.1-pro-preview" in rendered
    # Проверяем общий формат
    assert "🦀 **Krab Status**" in rendered
    assert "━━━━━━━━━━━━" in rendered
