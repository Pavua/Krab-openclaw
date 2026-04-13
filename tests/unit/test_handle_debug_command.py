# -*- coding: utf-8 -*-
"""
Юнит-тесты команды !debug (handle_debug).

Покрывает:
- owner-only проверку (не-владелец получает UserInputError)
- сводку по умолчанию (!debug)
- субкоманду !debug tasks
- субкоманду !debug sessions
- субкоманду !debug gc
- graceful деградацию при отсутствующих атрибутах
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_debug


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_access_profile(level: AccessLevel) -> SimpleNamespace:
    return SimpleNamespace(level=level)


def _make_bot(is_owner: bool = True) -> SimpleNamespace:
    """Минимальный bot stub для !debug тестов."""
    level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    profile = _make_access_profile(level)

    bot = SimpleNamespace(
        me=SimpleNamespace(id=777),
    )
    bot._get_access_profile = lambda user: profile
    bot._get_command_args = lambda msg: getattr(msg, "_args", "")
    return bot


def _make_message(args: str = "") -> SimpleNamespace:
    """Минимальный message stub."""
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=111),
        reply=AsyncMock(),
        text=f"!debug {args}".strip(),
        _args=args,
    )
    return msg


# ---------------------------------------------------------------------------
# Owner-only guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_не_владелец_получает_ошибку() -> None:
    """Не-владелец получает UserInputError."""
    bot = _make_bot(is_owner=False)
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_debug(bot, msg)
    assert "владельцу" in (exc_info.value.user_message or "")


@pytest.mark.asyncio
async def test_debug_tasks_не_владелец_получает_ошибку() -> None:
    """Субкоманда tasks: не-владелец тоже получает ошибку."""
    bot = _make_bot(is_owner=False)
    msg = _make_message(args="tasks")

    with pytest.raises(UserInputError):
        await handle_debug(bot, msg)


# ---------------------------------------------------------------------------
# !debug (сводка по умолчанию)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_default_содержит_ключевые_секции(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сводка по умолчанию содержит все ожидаемые секции."""
    bot = _make_bot()
    msg = _make_message()

    # Патчим telegram_rate_limiter
    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30,
        "current_in_window": 2,
        "total_acquired": 100,
        "total_waited": 5,
    }
    import src.handlers.command_handlers as ch_mod
    monkeypatch.setattr(ch_mod, "_active_timers", {1: {}, 2: {}})

    # Патчим openclaw_client._sessions
    fake_sessions = {"telegram_123": deque([{"role": "user", "content": "hello"}])}
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", fake_sessions, raising=False)

    # Патчим proactive_watch
    monkeypatch.setattr(
        ch_mod.proactive_watch,
        "get_error_digest",
        lambda: [{"type": "TestError", "msg": "test message"}],
        raising=False,
    )

    with patch("src.core.telegram_rate_limiter.telegram_rate_limiter", rl_mock):
        # Импортируем модуль rate_limiter чтобы патч применился внутри handle_debug
        import src.core.telegram_rate_limiter as rl_module
        monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
        await handle_debug(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "Debug Info" in text
    assert "asyncio tasks" in text
    assert "Pending timers" in text
    assert "OpenClaw sessions" in text
    assert "Rate limiter" in text
    assert "Last error" in text


@pytest.mark.asyncio
async def test_debug_default_показывает_количество_таймеров(monkeypatch: pytest.MonkeyPatch) -> None:
    """Количество pending timers правильно отображается в сводке."""
    bot = _make_bot()
    msg = _make_message()

    import src.handlers.command_handlers as ch_mod
    import src.core.telegram_rate_limiter as rl_module

    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30, "current_in_window": 0,
        "total_acquired": 0, "total_waited": 0,
    }
    monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
    monkeypatch.setattr(ch_mod, "_active_timers", {10: {}, 20: {}, 30: {}})
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", {}, raising=False)
    monkeypatch.setattr(ch_mod.proactive_watch, "get_error_digest", lambda: [], raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "`3`" in text  # 3 pending timers


@pytest.mark.asyncio
async def test_debug_default_без_сессий(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сводка без сессий — не падает, session_count = 0."""
    bot = _make_bot()
    msg = _make_message()

    import src.handlers.command_handlers as ch_mod
    import src.core.telegram_rate_limiter as rl_module

    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30, "current_in_window": 0,
        "total_acquired": 0, "total_waited": 0,
    }
    monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
    monkeypatch.setattr(ch_mod, "_active_timers", {})
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", {}, raising=False)
    monkeypatch.setattr(ch_mod.proactive_watch, "get_error_digest", lambda: [], raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "0" in text  # 0 сессий


@pytest.mark.asyncio
async def test_debug_default_нет_атрибута_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если _sessions недоступен — graceful fallback, не падает."""
    bot = _make_bot()
    msg = _make_message()

    import src.handlers.command_handlers as ch_mod
    import src.core.telegram_rate_limiter as rl_module

    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30, "current_in_window": 0,
        "total_acquired": 0, "total_waited": 0,
    }
    monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
    monkeypatch.setattr(ch_mod, "_active_timers", {})
    # Удаляем _sessions у openclaw_client
    if hasattr(ch_mod.openclaw_client, "_sessions"):
        monkeypatch.delattr(ch_mod.openclaw_client, "_sessions", raising=False)
    monkeypatch.setattr(ch_mod.proactive_watch, "get_error_digest", lambda: [], raising=False)

    await handle_debug(bot, msg)
    # Главное — не упало, reply вызван
    assert msg.reply.called


@pytest.mark.asyncio
async def test_debug_default_last_error_из_proactive_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Last error корректно подтягивается из proactive_watch."""
    bot = _make_bot()
    msg = _make_message()

    import src.handlers.command_handlers as ch_mod
    import src.core.telegram_rate_limiter as rl_module

    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30, "current_in_window": 0,
        "total_acquired": 0, "total_waited": 0,
    }
    monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
    monkeypatch.setattr(ch_mod, "_active_timers", {})
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", {}, raising=False)
    monkeypatch.setattr(
        ch_mod.proactive_watch,
        "get_error_digest",
        lambda: [{"type": "RuntimeError", "msg": "connection timeout"}],
        raising=False,
    )

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "RuntimeError" in text
    assert "connection timeout" in text


@pytest.mark.asyncio
async def test_debug_default_last_error_проactive_watch_недоступен(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если proactive_watch.get_error_digest() кидает исключение — не падает."""
    bot = _make_bot()
    msg = _make_message()

    import src.handlers.command_handlers as ch_mod
    import src.core.telegram_rate_limiter as rl_module

    rl_mock = MagicMock()
    rl_mock.stats.return_value = {
        "max_per_sec": 30, "current_in_window": 0,
        "total_acquired": 0, "total_waited": 0,
    }
    monkeypatch.setattr(rl_module, "telegram_rate_limiter", rl_mock)
    monkeypatch.setattr(ch_mod, "_active_timers", {})
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", {}, raising=False)
    monkeypatch.setattr(
        ch_mod.proactive_watch,
        "get_error_digest",
        MagicMock(side_effect=AttributeError("no such method")),
        raising=False,
    )

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    # last error — dash при ошибке
    assert "—" in text


# ---------------------------------------------------------------------------
# !debug tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_tasks_список_задач() -> None:
    """!debug tasks — reply содержит список asyncio задач."""
    bot = _make_bot()
    msg = _make_message(args="tasks")

    await handle_debug(bot, msg)

    text = msg.reply.call_args[0][0]
    assert "asyncio tasks" in text
    # Текущая задача должна быть в списке
    assert "running" in text or "done" in text


@pytest.mark.asyncio
async def test_debug_tasks_заголовок_содержит_count() -> None:
    """Заголовок содержит общее число задач."""
    bot = _make_bot()
    msg = _make_message(args="tasks")

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "total" in text


@pytest.mark.asyncio
async def test_debug_tasks_не_падает_при_многих_задачах(monkeypatch: pytest.MonkeyPatch) -> None:
    """При > 30 задачах — обрезает список и добавляет 'и ещё N'."""
    bot = _make_bot()
    msg = _make_message(args="tasks")

    # Создаём фиктивные завершённые задачи
    async def _noop():
        pass

    fake_tasks = []
    loop = asyncio.get_event_loop()
    for i in range(35):
        t = loop.create_task(_noop(), name=f"fake_task_{i}")
        fake_tasks.append(t)

    # Дождёмся завершения всех fake tasks
    await asyncio.gather(*fake_tasks, return_exceptions=True)

    # Патчим asyncio.all_tasks
    import src.handlers.command_handlers as ch_mod
    monkeypatch.setattr(asyncio, "all_tasks", lambda: fake_tasks[:35])

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "ещё" in text


# ---------------------------------------------------------------------------
# !debug sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_sessions_пустой_список(monkeypatch: pytest.MonkeyPatch) -> None:
    """!debug sessions — нет сессий → 'сессий нет'."""
    bot = _make_bot()
    msg = _make_message(args="sessions")

    import src.handlers.command_handlers as ch_mod
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", {}, raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "OpenClaw sessions" in text
    assert "сессий нет" in text


@pytest.mark.asyncio
async def test_debug_sessions_показывает_сессии(monkeypatch: pytest.MonkeyPatch) -> None:
    """!debug sessions — список сессий с размером и токенами."""
    bot = _make_bot()
    msg = _make_message(args="sessions")

    import src.handlers.command_handlers as ch_mod

    fake_sessions = {
        "telegram_111": deque([
            {"role": "user", "content": "Привет, как дела?"},
            {"role": "assistant", "content": "Отлично! Чем могу помочь?"},
        ]),
        "telegram_222": deque([
            {"role": "user", "content": "test"},
        ]),
    }
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", fake_sessions, raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "OpenClaw sessions" in text
    assert "telegram_111" in text
    assert "telegram_222" in text
    assert "2` сообщений" in text
    assert "1` сообщений" in text


@pytest.mark.asyncio
async def test_debug_sessions_сортировка_по_размеру(monkeypatch: pytest.MonkeyPatch) -> None:
    """!debug sessions — большие сессии идут первыми."""
    bot = _make_bot()
    msg = _make_message(args="sessions")

    import src.handlers.command_handlers as ch_mod

    big = deque([{"role": "user", "content": f"msg{i}"} for i in range(10)])
    small = deque([{"role": "user", "content": "only one"}])
    fake_sessions = {"session_small": small, "session_big": big}
    monkeypatch.setattr(ch_mod.openclaw_client, "_sessions", fake_sessions, raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    # session_big должна появиться раньше session_small
    assert text.index("session_big") < text.index("session_small")


@pytest.mark.asyncio
async def test_debug_sessions_нет_атрибута_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """!debug sessions — если _sessions недоступен, не падает."""
    bot = _make_bot()
    msg = _make_message(args="sessions")

    import src.handlers.command_handlers as ch_mod
    monkeypatch.delattr(ch_mod.openclaw_client, "_sessions", raising=False)

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "OpenClaw sessions" in text
    assert "сессий нет" in text


# ---------------------------------------------------------------------------
# !debug gc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_gc_вызывает_collect() -> None:
    """!debug gc — вызывает gc.collect() и отображает статистику."""
    bot = _make_bot()
    msg = _make_message(args="gc")

    import gc
    collected_calls = []
    original_collect = gc.collect

    def mock_collect():
        result = original_collect()
        collected_calls.append(result)
        return result

    with patch("gc.collect", side_effect=mock_collect):
        await handle_debug(bot, msg)

    assert len(collected_calls) == 1
    text = msg.reply.call_args[0][0]
    assert "Garbage Collection" in text


@pytest.mark.asyncio
async def test_debug_gc_содержит_поля_до_после() -> None:
    """!debug gc — вывод содержит поля До:/После: с gen0/gen1/gen2."""
    bot = _make_bot()
    msg = _make_message(args="gc")

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    assert "До:" in text
    assert "После:" in text
    assert "gen0=" in text
    assert "gen1=" in text
    assert "gen2=" in text
    assert "Собрано объектов" in text
    assert "gc.garbage" in text


@pytest.mark.asyncio
async def test_debug_gc_unreachable_count() -> None:
    """!debug gc — unreachable count отображается как число."""
    bot = _make_bot()
    msg = _make_message(args="gc")

    await handle_debug(bot, msg)
    text = msg.reply.call_args[0][0]
    # "gc.garbage (unreachable): `N`"
    assert "unreachable" in text


# ---------------------------------------------------------------------------
# Регистрация в __init__ и userbot_bridge
# ---------------------------------------------------------------------------


def test_handle_debug_экспортируется_из_handlers() -> None:
    """handle_debug доступен через src.handlers."""
    from src.handlers import handle_debug as hd
    assert callable(hd)


def test_handle_debug_в_all_handlers() -> None:
    """handle_debug присутствует в __all__ пакета handlers."""
    import src.handlers as h
    assert "handle_debug" in h.__all__
