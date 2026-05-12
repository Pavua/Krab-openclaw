# -*- coding: utf-8 -*-
"""Wave 124: tests для scripts/krab_openclaw_watchdog.py + metrics/openclaw_health.

Покрытие:
1. probe healthy → consecutive_fails=0, gauge=1, no kickstart.
2. probe failed → consecutive_fails accumulates, gauge=0, counter[reason].
3. fails >= threshold + auto_restart=True → kickstart triggered + counter inc.
4. fails >= threshold + auto_restart=False → no kickstart (observe-only).
5. kickstart cooldown: повторный fail в окне cooldown — restart НЕ вызывается.
6. recovery: healthy probe сбрасывает consecutive_fails и обновляет last_healthy_ts.
7. corrupt state file → graceful fallback на default state.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import krab_openclaw_watchdog as wd


class _FakeMetrics:
    """In-memory заглушка вместо prometheus_client."""

    def __init__(self) -> None:
        self.last_healthy: bool | None = None
        self.last_reason: str | None = None
        self.fail_counter: dict[str, int] = {}
        self.restart_count: int = 0

    def record_probe_result(self, *, healthy: bool, reason: str | None = None) -> None:
        self.last_healthy = healthy
        self.last_reason = reason
        if not healthy:
            r = reason or "unknown"
            self.fail_counter[r] = self.fail_counter.get(r, 0) + 1

    def record_restart(self) -> None:
        self.restart_count += 1


def _kickstart_ok(_cmd: list[str]) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _kickstart_fail(_cmd: list[str]) -> SimpleNamespace:
    return SimpleNamespace(returncode=3, stdout="", stderr="boom")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_once_healthy_resets_state(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    state_path.write_text(json.dumps({"consecutive_fails": 2, "restart_count": 0}))
    metrics = _FakeMetrics()
    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=True,
        probe_fn=lambda: (True, None),
        kickstart_fn=lambda runner=None: (_ for _ in ()).throw(  # type: ignore[misc]
            AssertionError("must not kickstart on healthy")
        ),
        now_fn=lambda: 1000.0,
        metrics_module=metrics,
    )
    assert state["consecutive_fails"] == 0
    assert state["last_healthy_ts"] == 1000.0
    assert metrics.last_healthy is True


def test_run_once_failed_accumulates(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    metrics = _FakeMetrics()
    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=False,
        probe_fn=lambda: (False, "timeout"),
        now_fn=lambda: 500.0,
        metrics_module=metrics,
    )
    assert state["consecutive_fails"] == 1
    assert state["last_reason"] == "timeout"
    assert metrics.last_healthy is False
    assert metrics.fail_counter == {"timeout": 1}


def test_threshold_triggers_kickstart_when_auto_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    state_path.write_text(
        json.dumps({"consecutive_fails": 2, "last_restart_ts": 0.0, "restart_count": 0})
    )
    metrics = _FakeMetrics()
    kicks: list[list[str]] = []

    def runner(cmd: list[str]) -> SimpleNamespace:
        kicks.append(cmd)
        return _kickstart_ok(cmd)

    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=True,
        probe_fn=lambda: (False, "connection_refused"),
        kickstart_fn=lambda: wd.kickstart_gateway(runner=runner),
        now_fn=lambda: 2000.0,
        metrics_module=metrics,
    )
    assert state["restart_count"] == 1
    assert state["consecutive_fails"] == 0  # reset чтобы дать шанс
    assert state["last_restart_ts"] == 2000.0
    assert metrics.restart_count == 1
    assert kicks and kicks[0][0:3] == ["launchctl", "kickstart", "-k"]


def test_threshold_no_kickstart_without_auto_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    state_path.write_text(
        json.dumps({"consecutive_fails": 5, "last_restart_ts": 0.0, "restart_count": 0})
    )
    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=False,
        probe_fn=lambda: (False, "timeout"),
        kickstart_fn=lambda: (_ for _ in ()).throw(  # type: ignore[misc]
            AssertionError("must not kickstart without auto_restart")
        ),
        now_fn=lambda: 100.0,
    )
    assert state["consecutive_fails"] == 6
    assert state["restart_count"] == 0


def test_kickstart_cooldown_blocks_restart_burst(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    # last_restart 30 сек назад, cooldown 120 сек → restart НЕ должен случиться.
    state_path.write_text(
        json.dumps(
            {"consecutive_fails": 3, "last_restart_ts": 1000.0, "restart_count": 1}
        )
    )
    kicks: list[Any] = []
    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=True,
        probe_fn=lambda: (False, "timeout"),
        kickstart_fn=lambda: kicks.append("called") or (True, "ok"),  # type: ignore[func-returns-value]
        now_fn=lambda: 1030.0,
    )
    assert kicks == [], "cooldown должен заблокировать повторный kickstart"
    assert state["restart_count"] == 1
    assert state["consecutive_fails"] == 4


def test_healthy_after_failures_resets_counter(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    state_path.write_text(
        json.dumps({"consecutive_fails": 2, "last_reason": "timeout"})
    )
    state = wd.run_once(
        state_path=state_path,
        fail_threshold=3,
        auto_restart=True,
        probe_fn=lambda: (True, None),
        kickstart_fn=lambda: (True, "ok"),
        now_fn=lambda: 3000.0,
    )
    assert state["consecutive_fails"] == 0
    assert state["last_reason"] is None
    assert state["last_healthy_ts"] == 3000.0


def test_corrupt_state_file_recovers_to_default(tmp_path: Path) -> None:
    state_path = tmp_path / "wd.json"
    state_path.write_text("{this is not json")
    loaded = wd.load_state(state_path)
    assert loaded["consecutive_fails"] == 0
    assert loaded["restart_count"] == 0


def test_metrics_module_records_probe_and_restart(tmp_path: Path) -> None:
    """Sanity-check helper'ов из src.core.metrics.openclaw_health."""
    from src.core.metrics import openclaw_health as oh

    # Сами по себе должны не падать, даже без prometheus_client.
    oh.record_probe_result(healthy=True)
    oh.record_probe_result(healthy=False, reason="timeout")
    oh.record_restart()
