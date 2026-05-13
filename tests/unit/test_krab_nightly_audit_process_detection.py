# -*- coding: utf-8 -*-
"""Wave 196: тесты process-detection в nightly_self_audit через launchctl.

Покрывают:
- launchctl output parsing (формат PID EXIT LABEL)
- pid-present → running (alive)
- "-" → loaded_idle (process down)
- ai.krab.core отсутствует в выводе → not_loaded
- subprocess error → fallback на psutil
- psutil fallback находит python -m src.main
"""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from src.core.nightly_self_audit import (
    _check_krab_via_launchctl,
    _check_krab_via_psutil,
    audit_process_health,
)

# ---------------------------------------------------------------------------
# launchctl output parsing
# ---------------------------------------------------------------------------


def test_launchctl_running_with_pid():
    """ai.krab.core с PID present → state=running."""
    out = "46408\t0\tai.krab.core\n12346\t0\tai.krab.inbox-watcher\n"
    mock_result = MagicMock(stdout=out, returncode=0)
    with patch("subprocess.run", return_value=mock_result):
        state, pid, raw = _check_krab_via_launchctl()
    assert state == "running"
    assert pid == 46408
    assert "ai.krab.core" in raw


def test_launchctl_loaded_idle_dash_pid():
    """ai.krab.core с '-' в PID → state=loaded_idle (process down)."""
    out = "-\t0\tai.krab.core\n12346\t0\tai.krab.inbox-watcher\n"
    mock_result = MagicMock(stdout=out, returncode=0)
    with patch("subprocess.run", return_value=mock_result):
        state, pid, raw = _check_krab_via_launchctl()
    assert state == "loaded_idle"
    assert pid is None


def test_launchctl_not_loaded_label_missing():
    """Если ai.krab.core отсутствует в выводе → state=not_loaded."""
    out = "12346\t0\tai.krab.inbox-watcher\n12347\t0\tai.krab.voice-gateway\n"
    mock_result = MagicMock(stdout=out, returncode=0)
    with patch("subprocess.run", return_value=mock_result):
        state, pid, raw = _check_krab_via_launchctl()
    assert state == "not_loaded"
    assert pid is None


def test_launchctl_subprocess_error_returns_error_state():
    """launchctl недоступен / OSError → state=error."""
    with patch("subprocess.run", side_effect=FileNotFoundError("launchctl gone")):
        state, pid, raw = _check_krab_via_launchctl()
    assert state == "error"
    assert pid is None
    assert "launchctl" in raw


# ---------------------------------------------------------------------------
# psutil fallback
# ---------------------------------------------------------------------------


def test_psutil_fallback_finds_python_dash_m_src_main():
    """Fallback находит процесс с cmdline=['python', '-m', 'src.main']."""
    mock_proc = MagicMock()
    mock_proc.info = {"cmdline": ["/usr/bin/python3.13", "-m", "src.main"]}
    mock_proc.pid = 99999

    mock_psutil = MagicMock()
    mock_psutil.process_iter.return_value = [mock_proc]
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        pid = _check_krab_via_psutil()
    assert pid == 99999


def test_psutil_fallback_returns_none_when_no_match():
    """Fallback возвращает None если ни один процесс не подходит."""
    other_proc = MagicMock()
    other_proc.info = {"cmdline": ["/bin/zsh", "-l"]}
    other_proc.pid = 1

    mock_psutil = MagicMock()
    mock_psutil.process_iter.return_value = [other_proc]
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        pid = _check_krab_via_psutil()
    assert pid is None


# ---------------------------------------------------------------------------
# audit_process_health integration: critical wording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_process_health_critical_wording_includes_launchctl_state():
    """🔴 'Krab not running (launchctl: loaded_idle)' — новый wording."""
    out = "-\t0\tai.krab.core\n"
    mock_result = MagicMock(stdout=out, returncode=0)

    mock_psutil = MagicMock()
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        with patch("subprocess.run", return_value=mock_result):
            finding = await audit_process_health()

    assert finding.status == "critical"
    assert "Krab not running" in finding.summary
    assert "loaded_idle" in finding.summary
    # старая формулировка ушла
    assert "не найден" not in finding.summary


@pytest.mark.asyncio
async def test_audit_process_health_launchctl_error_uses_psutil_fallback():
    """Если launchctl error, но psutil находит python -m src.main → ok/warn."""
    mock_proc = MagicMock()
    mock_proc.info = {"cmdline": ["/usr/bin/python", "-m", "src.main"]}
    mock_proc.pid = 46408
    mock_proc.create_time.return_value = time.time() - 7200  # 2h uptime

    mock_psutil = MagicMock()
    mock_psutil.process_iter.return_value = [mock_proc]
    mock_psutil.Process.return_value = mock_proc
    mock_psutil.NoSuchProcess = Exception
    mock_psutil.AccessDenied = Exception

    call_count = {"n": 0}

    def fake_run(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Первый вызов из _check_krab_via_launchctl — ошибка
            raise OSError("launchctl unavailable")
        # Второй вызов (для подсчёта daemons) — пустой stdout, returncode 0
        return MagicMock(stdout="", returncode=0)

    with patch.dict("sys.modules", {"psutil": mock_psutil}):
        with patch("subprocess.run", side_effect=fake_run):
            finding = await audit_process_health()

    # launchctl error + psutil found pid → не critical (ok/warn зависит от daemons)
    assert finding.status in ("ok", "warn")
    assert "Uptime" in finding.summary
