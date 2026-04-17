# -*- coding: utf-8 -*-
"""
Тесты auto_restart_policy: rate-limit, cooldown, subprocess mocking,
Telegram notification callback.

Покрываем:
1) AUTO_RESTART_ENABLED=false → restart skipped;
2) rate-limit: 4-я попытка в час → skip;
3) экспоненциальный cooldown: 60s → 120s → 300s → 600s;
4) сброс consecutive_failures при success;
5) subprocess.run вызывается с clean_subprocess_env;
6) TimeoutExpired → success=False, reason=timeout;
7) notification callback вызывается с сообщением.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.core.auto_restart_policy import (
    DEFAULT_COOLDOWNS_SEC,
    DEFAULT_MAX_ATTEMPTS_PER_HOUR,
    AutoRestartManager,
    ServiceRestartState,
)

# --------------------------------------------------------------------
# ServiceRestartState unit tests
# --------------------------------------------------------------------


def test_fresh_state_can_restart() -> None:
    """Свежее состояние всегда разрешает restart."""
    state = ServiceRestartState(service_name="svc")
    can, reason = state.can_restart()
    assert can is True
    assert reason == ""


def test_rate_limit_blocks_after_max_attempts() -> None:
    """После N попыток в час — rate_limit_exceeded."""
    state = ServiceRestartState(service_name="svc")
    now = datetime.now(timezone.utc)
    # Добавляем max attempts внутри последнего часа
    for i in range(DEFAULT_MAX_ATTEMPTS_PER_HOUR):
        state.attempts.append(now - timedelta(minutes=i * 10))

    can, reason = state.can_restart(now=now)
    assert can is False
    assert "rate_limit_exceeded" in reason


def test_rate_limit_gc_drops_old_attempts() -> None:
    """Попытки старше 1 часа не должны блокировать."""
    state = ServiceRestartState(service_name="svc")
    now = datetime.now(timezone.utc)
    # Ставим N попыток, все старше часа
    for i in range(DEFAULT_MAX_ATTEMPTS_PER_HOUR + 5):
        state.attempts.append(now - timedelta(hours=2, minutes=i))

    can, reason = state.can_restart(now=now)
    assert can is True
    assert reason == ""
    # GC должен обнулить attempts
    assert state.attempts == []


def test_cooldown_exponential_progression() -> None:
    """Cooldown растёт при consecutive_failures: 60s → 120s → 300s → 600s (cap)."""
    state = ServiceRestartState(service_name="svc")
    now = datetime.now(timezone.utc)

    for failures, expected_cd in enumerate(DEFAULT_COOLDOWNS_SEC):
        state.consecutive_failures = failures
        state.last_restart = now - timedelta(seconds=expected_cd - 1)
        # В пределах cooldown — нельзя
        can, reason = state.can_restart(now=now)
        assert can is False, f"failures={failures} should block"
        assert f"cooldown:{expected_cd}s" in reason

        # После cooldown — можно
        state.last_restart = now - timedelta(seconds=expected_cd + 1)
        can, _ = state.can_restart(now=now)
        assert can is True, f"failures={failures} should unblock after cooldown"


def test_cooldown_caps_at_last_entry() -> None:
    """Consecutive_failures > len(cooldowns) использует последний entry."""
    state = ServiceRestartState(service_name="svc")
    now = datetime.now(timezone.utc)
    max_cd = DEFAULT_COOLDOWNS_SEC[-1]

    state.consecutive_failures = 99  # намного больше len(cooldowns)
    state.last_restart = now - timedelta(seconds=max_cd - 10)
    can, reason = state.can_restart(now=now)
    assert can is False
    assert f"cooldown:{max_cd}s" in reason


def test_record_attempt_resets_failures_on_success() -> None:
    """Успешный restart обнуляет consecutive_failures."""
    state = ServiceRestartState(service_name="svc", consecutive_failures=3)
    state.record_attempt(success=True)
    assert state.consecutive_failures == 0
    assert state.last_restart is not None


def test_record_attempt_increments_on_failure() -> None:
    """Failed restart инкрементирует consecutive_failures."""
    state = ServiceRestartState(service_name="svc", consecutive_failures=1)
    state.record_attempt(success=False)
    assert state.consecutive_failures == 2


# --------------------------------------------------------------------
# AutoRestartManager integration tests
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_restart_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTO_RESTART_ENABLED=false → сразу skip без subprocess."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "false")
    mgr = AutoRestartManager()

    subprocess_mock = MagicMock()
    monkeypatch.setattr(subprocess, "run", subprocess_mock)

    success, reason = await mgr.attempt_restart("svc", ["echo", "hi"])
    assert success is False
    assert reason == "disabled_by_env"
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_auto_restart_rate_limit_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """4-я попытка в час возвращает rate_limit_exceeded без subprocess."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    # Pre-fill state: N попыток в последний час
    state = mgr.get_state("svc")
    now = datetime.now(timezone.utc)
    for i in range(DEFAULT_MAX_ATTEMPTS_PER_HOUR):
        state.attempts.append(now - timedelta(minutes=i * 5))

    subprocess_mock = MagicMock()
    monkeypatch.setattr(subprocess, "run", subprocess_mock)

    success, reason = await mgr.attempt_restart("svc", ["echo", "hi"])
    assert success is False
    assert "rate_limit_exceeded" in reason
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_auto_restart_cooldown_reset_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful restart сбрасывает consecutive_failures в 0."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()
    state = mgr.get_state("svc")
    state.consecutive_failures = 3  # ранее накопленные fails

    completed = MagicMock()
    completed.returncode = 0
    completed.stderr = ""
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

    success, reason = await mgr.attempt_restart("svc", ["echo", "ok"])
    assert success is True
    assert reason == "ok"
    assert state.consecutive_failures == 0


@pytest.mark.asyncio
async def test_restart_command_executed_with_clean_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subprocess.run должен быть вызван с env=clean_subprocess_env()."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    completed = MagicMock()
    completed.returncode = 0
    completed.stderr = ""
    run_mock = MagicMock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", run_mock)

    success, _ = await mgr.attempt_restart("svc", ["launchctl", "kickstart", "-k", "foo"])
    assert success is True
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs.get("env") is not None
    # Malloc-debug ключи должны быть вычищены
    env = kwargs["env"]
    assert "MallocStackLogging" not in env
    assert kwargs.get("check") is False
    assert kwargs.get("capture_output") is True


@pytest.mark.asyncio
async def test_restart_timeout_marks_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subprocess TimeoutExpired → success=False, reason='timeout'."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    def raise_timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=30)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    success, reason = await mgr.attempt_restart("svc", ["sleep", "9999"])
    assert success is False
    assert reason == "timeout"
    # Consecutive failures инкрементируется
    assert mgr.get_state("svc").consecutive_failures == 1


@pytest.mark.asyncio
async def test_restart_nonzero_exit_marks_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """returncode != 0 → success=False, reason='cmd_failed'."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    completed = MagicMock()
    completed.returncode = 1
    completed.stderr = "boom"
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

    success, reason = await mgr.attempt_restart("svc", ["false"])
    assert success is False
    assert reason == "cmd_failed"
    assert mgr.get_state("svc").consecutive_failures == 1


@pytest.mark.asyncio
async def test_restart_os_error_marks_exec_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError (e.g. binary not found) → reason='exec_error'."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    def raise_oserror(*_a: object, **_k: object) -> object:
        raise OSError("no such file")

    monkeypatch.setattr(subprocess, "run", raise_oserror)

    success, reason = await mgr.attempt_restart("svc", ["/nonexistent/bin"])
    assert success is False
    assert reason == "exec_error"


@pytest.mark.asyncio
async def test_notification_callback_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set_notification_callback: на каждую попытку вызывается callback."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    messages: list[str] = []

    async def fake_notify(msg: str) -> None:
        messages.append(msg)

    mgr.set_notification_callback(fake_notify)

    completed = MagicMock()
    completed.returncode = 0
    completed.stderr = ""
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

    await mgr.attempt_restart("openclaw_gateway", ["echo", "ok"])
    assert len(messages) == 1
    assert "openclaw_gateway" in messages[0]
    assert "OK" in messages[0]


@pytest.mark.asyncio
async def test_notification_callback_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка в callback не должна ломать attempt_restart."""
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    mgr = AutoRestartManager()

    async def broken_notify(_msg: str) -> None:
        raise RuntimeError("telegram down")

    mgr.set_notification_callback(broken_notify)

    completed = MagicMock()
    completed.returncode = 0
    completed.stderr = ""
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

    # Не должно бросать
    success, _ = await mgr.attempt_restart("svc", ["echo", "ok"])
    assert success is True


def test_manager_status_reflects_state() -> None:
    """status() возвращает snapshot всех per-service состояний."""
    mgr = AutoRestartManager()
    state = mgr.get_state("openclaw_gateway")
    state.consecutive_failures = 2
    state.last_restart = datetime.now(timezone.utc)
    state.attempts = [datetime.now(timezone.utc)]

    snapshot = mgr.status()
    assert "openclaw_gateway" in snapshot["services"]
    info = snapshot["services"]["openclaw_gateway"]
    assert info["consecutive_failures"] == 2
    assert info["attempts_last_hour"] == 1
    assert info["last_restart"] is not None
    assert snapshot["max_attempts_per_hour"] == DEFAULT_MAX_ATTEMPTS_PER_HOUR
