# -*- coding: utf-8 -*-
"""
Тесты auto_restart_policy — launchd-aware self-healing для сервисов.

Покрываем:
1) is_service_loaded_in_launchd — true/false по returncode и state = not loaded
2) bootstrap_service_if_unloaded — skip если loaded, запуск launchctl если нет
3) AutoRestartPolicy.attempt_restart — disabled_by_env, unknown_service,
   cooldown, launchd bootstrap path, restart cmd path, error paths
"""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import MagicMock

# ─── is_service_loaded_in_launchd ────────────────────────────────────────────


def test_is_service_loaded_returns_true_when_running(monkeypatch):
    mock_result = MagicMock(returncode=0, stdout="state = running\npid = 12345\n")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
    from src.core.auto_restart_policy import is_service_loaded_in_launchd

    assert is_service_loaded_in_launchd("ai.openclaw.gateway") is True


def test_is_service_loaded_returns_false_when_not_loaded(monkeypatch):
    mock_result = MagicMock(returncode=113, stdout="", stderr="Could not find service")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
    from src.core.auto_restart_policy import is_service_loaded_in_launchd

    assert is_service_loaded_in_launchd("nonexistent") is False


def test_is_service_loaded_returns_false_when_state_not_loaded(monkeypatch):
    """Иногда launchctl возвращает rc=0, но stdout содержит `state = not loaded`."""
    mock_result = MagicMock(
        returncode=0,
        stdout="service = {\n  state = not loaded\n  label = ai.test\n}\n",
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
    from src.core.auto_restart_policy import is_service_loaded_in_launchd

    assert is_service_loaded_in_launchd("ai.test") is False


def test_is_service_loaded_returns_false_on_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["launchctl"], timeout=5)

    monkeypatch.setattr("subprocess.run", _raise)
    from src.core.auto_restart_policy import is_service_loaded_in_launchd

    assert is_service_loaded_in_launchd("ai.test") is False


def test_is_service_loaded_returns_false_on_oserror(monkeypatch):
    def _raise(*a, **kw):
        raise OSError("no launchctl")

    monkeypatch.setattr("subprocess.run", _raise)
    from src.core.auto_restart_policy import is_service_loaded_in_launchd

    assert is_service_loaded_in_launchd("ai.test") is False


# ─── bootstrap_service_if_unloaded ───────────────────────────────────────────


def test_bootstrap_service_skip_if_loaded(monkeypatch):
    monkeypatch.setattr(
        "src.core.auto_restart_policy.is_service_loaded_in_launchd",
        lambda _label: True,
    )
    from src.core.auto_restart_policy import bootstrap_service_if_unloaded

    did, reason = bootstrap_service_if_unloaded("ai.test", "/tmp/test.plist")
    assert did is False
    assert reason == "already_loaded"


def test_bootstrap_service_runs_launchctl_when_unloaded(monkeypatch):
    monkeypatch.setattr(
        "src.core.auto_restart_policy.is_service_loaded_in_launchd",
        lambda _label: False,
    )
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
    from src.core.auto_restart_policy import bootstrap_service_if_unloaded

    did, reason = bootstrap_service_if_unloaded("ai.test", "/tmp/test.plist")
    assert did is True
    assert "bootstrap_ok" in reason


def test_bootstrap_service_reports_failure_when_launchctl_fails(monkeypatch):
    monkeypatch.setattr(
        "src.core.auto_restart_policy.is_service_loaded_in_launchd",
        lambda _label: False,
    )
    mock_result = MagicMock(returncode=5, stdout="", stderr="Bootstrap failed: E")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
    from src.core.auto_restart_policy import bootstrap_service_if_unloaded

    did, reason = bootstrap_service_if_unloaded("ai.test", "/tmp/test.plist")
    assert did is True
    assert reason.startswith("bootstrap_failed")


def test_bootstrap_service_handles_timeout(monkeypatch):
    monkeypatch.setattr(
        "src.core.auto_restart_policy.is_service_loaded_in_launchd",
        lambda _label: False,
    )

    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["launchctl"], timeout=10)

    monkeypatch.setattr("subprocess.run", _raise)
    from src.core.auto_restart_policy import bootstrap_service_if_unloaded

    did, reason = bootstrap_service_if_unloaded("ai.test", "/tmp/test.plist")
    assert did is True
    assert reason.startswith("bootstrap_error")


# ─── AutoRestartPolicy.attempt_restart ───────────────────────────────────────


def test_attempt_restart_disabled_by_env(monkeypatch):
    monkeypatch.delenv("AUTO_RESTART_ENABLED", raising=False)
    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is False
    assert reason == "disabled_by_env"


def test_attempt_restart_unknown_service(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "true")
    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("ghost_service"))
    assert ok is False
    assert reason == "unknown_service"


def test_attempt_restart_bootstraps_when_unloaded(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (True, "bootstrap_ok"),
    )
    # Restart cmd не должен вызваться — bootstrap вернул did=True.
    called = {"subprocess_run": 0}

    def _should_not_run(*a, **kw):
        called["subprocess_run"] += 1
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _should_not_run)

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is True
    assert reason == "bootstrap_ok"
    assert called["subprocess_run"] == 0


def test_attempt_restart_bootstrap_failed(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (True, "bootstrap_failed: E"),
    )

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is False
    assert reason.startswith("bootstrap_failed")


def test_attempt_restart_runs_cmd_when_loaded(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (False, "already_loaded"),
    )

    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is True
    assert reason == "restart_ok"


def test_attempt_restart_cmd_failed(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (False, "already_loaded"),
    )

    mock_result = MagicMock(returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is False
    assert reason.startswith("restart_failed")


def test_attempt_restart_cmd_timeout(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (False, "already_loaded"),
    )

    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["bash"], timeout=30)

    monkeypatch.setattr("subprocess.run", _raise)

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    ok, reason = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok is False
    assert reason.startswith("restart_error")


def test_attempt_restart_respects_cooldown(monkeypatch):
    monkeypatch.setenv("AUTO_RESTART_ENABLED", "1")
    monkeypatch.setattr(
        "src.core.auto_restart_policy.bootstrap_service_if_unloaded",
        lambda _label, _plist: (False, "already_loaded"),
    )
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

    from src.core.auto_restart_policy import AutoRestartPolicy

    policy = AutoRestartPolicy()
    # Первый вызов — успешный restart.
    ok1, r1 = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok1 is True and r1 == "restart_ok"

    # Второй подряд — должен попасть в cooldown.
    ok2, r2 = asyncio.run(policy.attempt_restart("openclaw_gateway"))
    assert ok2 is False
    assert r2 == "cooldown"


def test_services_has_expected_entries():
    from src.core.auto_restart_policy import SERVICES

    assert "openclaw_gateway" in SERVICES
    assert "mcp_yung_nagato" in SERVICES
    for name, cfg in SERVICES.items():
        assert "restart_cmd" in cfg, f"{name} missing restart_cmd"
        assert "launchd_label" in cfg, f"{name} missing launchd_label"
        assert "plist_path" in cfg, f"{name} missing plist_path"


def test_restart_commands_backward_compat():
    """Legacy call sites читают плоский RESTART_COMMANDS map."""
    from src.core.auto_restart_policy import RESTART_COMMANDS, SERVICES

    assert set(RESTART_COMMANDS.keys()) == set(SERVICES.keys())
    for name in SERVICES:
        assert RESTART_COMMANDS[name] == SERVICES[name]["restart_cmd"]
