# -*- coding: utf-8 -*-
"""
Smoke-тесты каноничного restart_core_hard.command.

Важно:
- Тест не должен убивать реальные процессы.
- Поэтому проверка запускается только в DRY RUN режиме.
"""

import os
import stat
import subprocess
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "restart_core_hard.command"


def test_restart_core_hard_script_exists_and_executable() -> None:
    script = _script_path()
    assert script.exists(), "restart_core_hard.command не найден"
    mode = script.stat().st_mode
    assert bool(mode & stat.S_IXUSR), "restart_core_hard.command должен быть исполняемым"


def test_restart_core_hard_script_dry_run_smoke() -> None:
    script = _script_path()
    env = os.environ.copy()
    env["KRAB_RESTART_DRY_RUN"] = "1"

    result = subprocess.run(
        ["/bin/zsh", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=25,
        check=False,
    )
    merged_output = f"{result.stdout}\n{result.stderr}"

    assert result.returncode == 0, merged_output
    assert "DRY RUN" in merged_output
    assert "KRAB CORE HARD RESTART" in merged_output


def test_restart_core_hard_script_has_required_guards() -> None:
    script = _script_path()
    content = script.read_text(encoding="utf-8")

    # Сигнатуры обязательных шагов (stop -> wait -> kill -> start -> pid).
    assert "kill -TERM" in content
    assert "kill -KILL" in content
    assert "krab_core.pid" in content
    assert "-m src.main" in content
