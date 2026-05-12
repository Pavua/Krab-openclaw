"""Wave 84: тесты OrbStack idle auto-stop."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import krab_orbstack_idle_stop as mod


class FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_runner(responses: dict[tuple[str, ...], FakeResult | Exception]) -> Any:
    """Возвращает fake subprocess.run; ключ — tuple argv."""

    def _run(argv: list[str], **_kwargs: Any) -> FakeResult:
        key = tuple(argv)
        resp = responses.get(key)
        if resp is None:
            raise AssertionError(f"unexpected argv {argv}")
        if isinstance(resp, Exception):
            raise resp
        return resp

    return _run


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "orbstack_idle.json"


def test_env_gate_off_returns_disabled(state_path: Path) -> None:
    """KRAB_ORBSTACK_IDLE_AUTO_STOP != 1 → action=disabled, без CLI вызовов."""
    payload = mod.run(
        state_path=state_path,
        runner=make_runner({}),  # никаких subprocess вызовов
        now_fn=lambda: 1_000_000.0,
        env={},
    )
    assert payload["action"] == "disabled"
    assert not state_path.exists()


def test_containers_running_keeps_state_and_updates_activity(state_path: Path) -> None:
    """Running containers → action=kept + last_activity_ts == now."""
    now = 2_000_000.0
    # state c устаревшим last_activity_ts
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_activity_ts": now - 10_000}))
    runner = make_runner({
        ("orb", "status"): FakeResult(0, "Running"),
        ("orb", "ps", "-q"): FakeResult(0, "abc123\ndef456\n"),
    })
    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: now,
        env={mod.ENV_GATE: "1"},
    )
    assert payload["action"] == "kept"
    assert payload["containers_running"] == 2
    assert payload["idle_since_sec"] == 0
    saved = json.loads(state_path.read_text())
    assert saved["last_activity_ts"] == now


def test_idle_below_threshold_does_not_stop(state_path: Path) -> None:
    """Контейнеров нет, но idle < 3600s → kept."""
    now = 3_000_000.0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_activity_ts": now - 500}))
    runner = make_runner({
        ("orb", "status"): FakeResult(0, "Running"),
        ("orb", "ps", "-q"): FakeResult(0, ""),
    })
    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: now,
        env={mod.ENV_GATE: "1"},
    )
    assert payload["action"] == "kept"
    assert payload["containers_running"] == 0
    assert payload["idle_since_sec"] == 500
    saved = json.loads(state_path.read_text())
    # last_activity_ts не обновлён — копим idle
    assert saved["last_activity_ts"] == now - 500


def test_idle_above_threshold_triggers_stop(state_path: Path) -> None:
    """0 containers + idle > 3600s → orb stop."""
    now = 4_000_000.0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_activity_ts": now - 5000}))
    stop_called = {"hit": False}

    def runner(argv: list[str], **_kwargs: Any) -> FakeResult:
        if argv == ["orb", "status"]:
            return FakeResult(0, "Running")
        if argv == ["orb", "ps", "-q"]:
            return FakeResult(0, "")
        if argv == ["orb", "stop"]:
            stop_called["hit"] = True
            return FakeResult(0, "")
        raise AssertionError(f"unexpected {argv}")

    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: now,
        env={mod.ENV_GATE: "1"},
    )
    assert stop_called["hit"] is True
    assert payload["action"] == "stopped"
    assert payload["idle_since_sec"] == 5000
    saved = json.loads(state_path.read_text())
    assert saved["last_action"] == "stopped"


def test_orbstack_already_off_returns_already_off(state_path: Path) -> None:
    """orb status non-zero → already_off."""
    runner = make_runner({
        ("orb", "status"): FakeResult(1, "", "not running"),
    })
    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: 5_000_000.0,
        env={mod.ENV_GATE: "1"},
    )
    assert payload["action"] == "already_off"
    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert saved["last_action"] == "already_off"


def test_state_file_corrupted_recovers_gracefully(state_path: Path) -> None:
    """Битый JSON → используем now как baseline, ничего не падает."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")
    now = 6_000_000.0
    runner = make_runner({
        ("orb", "status"): FakeResult(0, "Running"),
        ("orb", "ps", "-q"): FakeResult(0, ""),
    })
    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: now,
        env={mod.ENV_GATE: "1"},
    )
    # idle_since == 0 потому что last_activity_ts default to now
    assert payload["action"] == "kept"
    assert payload["idle_since_sec"] == 0


def test_probe_failure_is_safe(state_path: Path) -> None:
    """`orb ps` fails → action=probe_failed, без stop."""
    runner = make_runner({
        ("orb", "status"): FakeResult(0, "Running"),
        ("orb", "ps", "-q"): FakeResult(1, "", "error"),
    })
    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: 7_000_000.0,
        env={mod.ENV_GATE: "1"},
    )
    assert payload["action"] == "probe_failed"
    assert payload["containers_running"] == -1


def test_subprocess_timeout_treated_as_probe_failure(state_path: Path) -> None:
    """Timeout в `orb ps` → containers=-1, probe_failed."""

    def runner(argv: list[str], **_kwargs: Any) -> FakeResult:
        if argv == ["orb", "status"]:
            return FakeResult(0, "Running")
        if argv == ["orb", "ps", "-q"]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=15)
        raise AssertionError(f"unexpected {argv}")

    payload = mod.run(
        state_path=state_path,
        runner=runner,
        now_fn=lambda: 8_000_000.0,
        env={mod.ENV_GATE: "1"},
    )
    assert payload["action"] == "probe_failed"
    assert payload["containers_running"] == -1
