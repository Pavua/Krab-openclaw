"""Тесты CLI-контракта autoswitch-заглушки для web-панели.

Зачем:
- `/api/openclaw/model-autoswitch/status` всегда вызывает скрипт с `--profile`.
- Если скрипт перестаёт принимать этот аргумент, owner UI получает 500 или
  зависает на `Syncing...`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "openclaw_model_autoswitch.py"


def test_openclaw_model_autoswitch_accepts_current_profile() -> None:
    """Autoswitch CLI должен принимать профиль `current` и возвращать JSON."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", "current", "--dry-run"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["mode"] == "dry-run"
    assert payload["profile"] == "current"


def test_openclaw_model_autoswitch_accepts_toggle_profile() -> None:
    """Autoswitch CLI должен принимать профиль `toggle` для apply endpoint."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--profile", "toggle"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["mode"] == "apply"
    assert payload["profile"] == "toggle"
