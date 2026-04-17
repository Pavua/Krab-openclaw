# -*- coding: utf-8 -*-
"""
Тесты `src/core/provider_failover.py`.

Покрытие:
1. Под threshold — failover НЕ триггерится.
2. На threshold (при PROVIDER_FAILOVER_ENABLED=true) — триггерится и вызывает callback.
3. record_success сбрасывает consecutive, сохраняет total-счётчики.
4. Cooldown блокирует повторный switch сразу после предыдущего.
5. Failing fallback пропускается — выбирается следующий кандидат.
6. Disabled by env — всегда возвращает disabled_by_env.
7. Notification callback вызывается с корректным сообщением.
8. Callback-исключение — failover помечается как failed, не падает.
9. `no_viable_fallback` когда все кандидаты равны current.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def enabled_env(monkeypatch: pytest.MonkeyPatch):
    """Включает PROVIDER_FAILOVER_ENABLED на время теста."""
    monkeypatch.setenv("PROVIDER_FAILOVER_ENABLED", "true")
    yield


@pytest.fixture
def disabled_env(monkeypatch: pytest.MonkeyPatch):
    """Явно выключает failover."""
    monkeypatch.setenv("PROVIDER_FAILOVER_ENABLED", "false")
    yield


@pytest.mark.asyncio
async def test_no_failover_under_threshold(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=3)
    p.record_failure("codex-cli", "transport_error")
    p.record_failure("codex-cli", "transport_error")
    # 2 failures, threshold=3.
    result = await p.maybe_failover("codex-cli", ["codex-cli", "google/gemini-3-pro-preview"])
    assert not result.triggered
    assert "under_threshold" in result.reason


@pytest.mark.asyncio
async def test_failover_triggers_at_threshold(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=3)
    for _ in range(3):
        p.record_failure("codex-cli", "transport_error")

    called: dict[str, object] = {"flag": False}

    async def mock_cb(from_p: str, to_p: str) -> None:
        called["flag"] = True
        called["from"] = from_p
        called["to"] = to_p

    p.set_failover_callback(mock_cb)

    result = await p.maybe_failover(
        "codex-cli", ["codex-cli", "google/gemini-3-pro-preview"]
    )
    assert result.triggered is True
    assert result.from_provider == "codex-cli"
    assert result.to_provider == "google/gemini-3-pro-preview"
    assert result.reason.startswith("threshold_exceeded")
    assert called["flag"] is True
    assert called["from"] == "codex-cli"
    assert called["to"] == "google/gemini-3-pro-preview"


@pytest.mark.asyncio
async def test_record_success_resets_consecutive(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=3)
    p.record_failure("codex-cli", "e1")
    p.record_failure("codex-cli", "e2")
    p.record_success("codex-cli")
    state = p.get_all_states()["codex-cli"]
    assert state.consecutive_failures == 0
    assert state.total_successes == 1
    assert state.total_failures == 2
    assert state.last_error_code == ""


@pytest.mark.asyncio
async def test_failover_cooldown_prevents_rapid_switch(enabled_env):
    """Сразу после успешного failover повторный — заблокирован cooldown'ом."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2, cooldown_sec=300)

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    p.set_failover_callback(noop_cb)

    # Первый failover.
    p.record_failure("A", "err")
    p.record_failure("A", "err")
    first = await p.maybe_failover("A", ["A", "B", "C"])
    assert first.triggered is True
    assert first.to_provider == "B"

    # Сразу роняем следующего — cooldown ещё не прошёл.
    p.record_failure("B", "err")
    p.record_failure("B", "err")
    second = await p.maybe_failover("B", ["B", "C"])
    assert second.triggered is False
    assert "cooldown" in second.reason


@pytest.mark.asyncio
async def test_skip_failing_fallback(enabled_env):
    """Если fallback[0] сам уже упал >= threshold — выбираем fallback[1]."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2)
    # Первый кандидат-fallback тоже валится.
    p.record_failure("B", "err")
    p.record_failure("B", "err")
    # Текущий провайдер — A, тоже достиг threshold.
    p.record_failure("A", "err")
    p.record_failure("A", "err")

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    p.set_failover_callback(noop_cb)

    result = await p.maybe_failover("A", ["A", "B", "C"])
    assert result.triggered is True
    assert result.to_provider == "C"


@pytest.mark.asyncio
async def test_disabled_by_env(disabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=1)
    p.record_failure("codex-cli", "err")

    result = await p.maybe_failover("codex-cli", ["codex-cli", "gemini"])
    assert result.triggered is False
    assert result.reason == "disabled_by_env"


@pytest.mark.asyncio
async def test_notification_callback_receives_message(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2)
    p.record_failure("A", "transport_error")
    p.record_failure("A", "transport_error")

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    captured: dict[str, str] = {}

    async def notify_cb(msg: str) -> None:
        captured["msg"] = msg

    p.set_failover_callback(noop_cb)
    p.set_notification_callback(notify_cb)

    result = await p.maybe_failover("A", ["A", "B"])
    assert result.triggered is True
    assert "msg" in captured
    assert "A" in captured["msg"]
    assert "B" in captured["msg"]
    assert "transport_error" in captured["msg"]


@pytest.mark.asyncio
async def test_callback_exception_prevents_switch(enabled_env):
    """Исключение в failover_callback не должно валить весь процесс."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2)
    p.record_failure("A", "err")
    p.record_failure("A", "err")

    async def bad_cb(from_p: str, to_p: str) -> None:
        raise RuntimeError("apply failed")

    p.set_failover_callback(bad_cb)

    result = await p.maybe_failover("A", ["A", "B"])
    assert result.triggered is False
    assert "callback_failed" in result.reason


@pytest.mark.asyncio
async def test_no_viable_fallback_when_chain_empty(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=1)
    p.record_failure("A", "err")

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    p.set_failover_callback(noop_cb)

    # Chain содержит только current — viable кандидата нет.
    result = await p.maybe_failover("A", ["A"])
    assert result.triggered is False
    assert result.reason == "no_viable_fallback"


@pytest.mark.asyncio
async def test_reset_clears_all_state(enabled_env):
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2)
    p.record_failure("A", "err")
    p.record_success("B")

    p.reset()
    assert p.get_all_states() == {}


@pytest.mark.asyncio
async def test_failover_resets_consecutive_counter(enabled_env):
    """После успешного failover счётчик failed провайдера сбрасывается в 0."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2)
    p.record_failure("A", "err")
    p.record_failure("A", "err")

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    p.set_failover_callback(noop_cb)
    result = await p.maybe_failover("A", ["A", "B"])
    assert result.triggered is True

    state = p.get_all_states()["A"]
    assert state.consecutive_failures == 0
    # total_failures сохраняется для статистики.
    assert state.total_failures == 2


@pytest.mark.asyncio
async def test_cooldown_respected_across_instances_of_same_policy(enabled_env):
    """Симулируем истечение cooldown через ручную настройку last_failover_at."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy(threshold=2, cooldown_sec=300)

    async def noop_cb(from_p: str, to_p: str) -> None:
        return None

    p.set_failover_callback(noop_cb)

    # Первый failover.
    p.record_failure("A", "err")
    p.record_failure("A", "err")
    first = await p.maybe_failover("A", ["A", "B"])
    assert first.triggered is True

    # Вручную «откатываем» таймер как будто cooldown уже прошёл.
    p._last_failover_at = datetime.now(timezone.utc) - timedelta(seconds=3600)

    p.record_failure("B", "err")
    p.record_failure("B", "err")
    second = await p.maybe_failover("B", ["B", "C"])
    assert second.triggered is True
    assert second.to_provider == "C"


def test_empty_provider_names_are_ignored(enabled_env):
    """Пустые/whitespace provider-имена не создают записей."""
    from src.core.provider_failover import ProviderFailoverPolicy

    p = ProviderFailoverPolicy()
    p.record_failure("", "err")
    p.record_failure("   ", "err")
    p.record_success("")
    assert p.get_all_states() == {}
