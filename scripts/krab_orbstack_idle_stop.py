#!/usr/bin/env python3
"""Wave 84: OrbStack idle auto-stop.

Останавливает OrbStack если простаивает > IDLE_THRESHOLD_SEC и нет контейнеров.
Сценарий запускается LaunchAgent каждые 15 минут.

Поведение:
- Если KRAB_ORBSTACK_IDLE_AUTO_STOP != "1" — exit clean (no action).
- `orb ps -q` пусто И OrbStack running И idle > 3600s → `orb stop`.
- `orb ps -q` непусто → обновить last_activity_ts.
- OrbStack уже остановлен → action="already_off".

State file: ~/.openclaw/krab_runtime_state/orbstack_idle.json
Output: одна строка JSON в stdout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Кэш по умолчанию для idle-порога (1 час)
DEFAULT_IDLE_THRESHOLD_SEC = 3600
DEFAULT_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "orbstack_idle.json"
ENV_GATE = "KRAB_ORBSTACK_IDLE_AUTO_STOP"


def _now() -> float:
    return time.time()


def _load_state(path: Path) -> dict[str, Any]:
    """Чтение state с грациозной обработкой повреждённого JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _orb_running(runner: Any = subprocess.run) -> bool:
    """OrbStack считается running если `orb status` exit 0 и stdout содержит running-маркер."""
    try:
        result = runner(
            ["orb", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    out = (result.stdout or "").lower() + (result.stderr or "").lower()
    return "running" in out


def _orb_container_count(runner: Any = subprocess.run) -> int:
    """Число running containers по `orb ps -q`. -1 при ошибке probe."""
    try:
        result = runner(
            ["orb", "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1
    if result.returncode != 0:
        return -1
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    return len(lines)


def _orb_stop(runner: Any = subprocess.run) -> bool:
    try:
        result = runner(
            ["orb", "stop"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run(
    state_path: Path = DEFAULT_STATE_PATH,
    idle_threshold_sec: int = DEFAULT_IDLE_THRESHOLD_SEC,
    runner: Any = subprocess.run,
    now_fn: Any = _now,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Основная процедура. Возвращает payload результата."""
    env_map = env if env is not None else os.environ
    timestamp = now_fn()

    payload: dict[str, Any] = {
        "timestamp": timestamp,
        "containers_running": 0,
        "idle_since_sec": 0,
        "action": "disabled",
    }

    # Env-gate: по умолчанию OFF чтобы не удивить пользователя
    if env_map.get(ENV_GATE, "0") != "1":
        return payload

    # Проверка наличия CLI
    if shutil.which("orb") is None:
        payload["action"] = "no_cli"
        return payload

    state = _load_state(state_path)
    last_activity_ts = float(state.get("last_activity_ts") or timestamp)

    if not _orb_running(runner):
        payload["action"] = "already_off"
        # Сохраняем последнюю активность как сейчас — fresh baseline после ручного старта
        state["last_activity_ts"] = timestamp
        state["last_check_ts"] = timestamp
        state["last_action"] = "already_off"
        _save_state(state_path, state)
        return payload

    container_count = _orb_container_count(runner)
    payload["containers_running"] = container_count

    if container_count > 0:
        # Активность — обновляем baseline
        state["last_activity_ts"] = timestamp
        state["last_check_ts"] = timestamp
        state["last_action"] = "kept"
        _save_state(state_path, state)
        payload["idle_since_sec"] = 0
        payload["action"] = "kept"
        return payload

    if container_count < 0:
        # Probe failed — не трогаем
        state["last_check_ts"] = timestamp
        state["last_action"] = "probe_failed"
        _save_state(state_path, state)
        payload["action"] = "probe_failed"
        return payload

    # Контейнеров нет — считаем idle
    idle_since = max(0.0, timestamp - last_activity_ts)
    payload["idle_since_sec"] = int(idle_since)

    if idle_since < idle_threshold_sec:
        state["last_check_ts"] = timestamp
        state["last_action"] = "kept_idle"
        # last_activity_ts НЕ обновляем — копим idle
        _save_state(state_path, state)
        payload["action"] = "kept"
        return payload

    # Idle достаточно — стопим
    stopped = _orb_stop(runner)
    state["last_check_ts"] = timestamp
    if stopped:
        state["last_action"] = "stopped"
        state["last_activity_ts"] = timestamp  # reset, чтобы при следующем старте копить заново
        payload["action"] = "stopped"
    else:
        state["last_action"] = "stop_failed"
        payload["action"] = "stop_failed"
    _save_state(state_path, state)
    return payload


def main() -> int:
    payload = run()
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
