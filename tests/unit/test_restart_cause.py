# -*- coding: utf-8 -*-
"""
S64 Wave 4: тесты `src.core.restart_cause` — отслеживание причины рестарта.

Покрываем:
1. test_startup_cause_after_launchd_exit_78: запись `_launchd_exit_78` →
   следующий `record_startup_cause()` ставит cause `previous_exit_via_launchd_exit_78`.
2. test_startup_cause_manual_restart: нет history → cause `cold_first_start`.
3. test_startup_cause_after_graceful_shutdown: graceful intent →
   cause `previous_clean_shutdown`.
4. test_startup_cause_after_crash: last_seen_pid отличается, нет intent →
   cause `previous_crash_or_kill`.
5. test_record_exit_intent_appends_jsonl: запись добавляет валидный JSON.
6. test_history_rotation_truncates: при > MAX_LINES обрезаем хвост.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Подсовываем restart_cause временный runtime_state dir через env."""
    monkeypatch.setenv("KRAB_RUNTIME_STATE_DIR", str(tmp_path))
    return tmp_path


def test_record_exit_intent_appends_jsonl(tmp_runtime_dir: Path) -> None:
    """record_exit_intent → одна валидная jsonl-строка с reason+exit_code."""
    from src.core.restart_cause import record_exit_intent

    record_exit_intent("dispatcher_starved_escalation", exit_code=78)

    path = tmp_runtime_dir / "krab_exit_history.jsonl"
    assert path.exists(), "krab_exit_history.jsonl должен быть создан"
    raw = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1, "должна быть ровно одна запись"
    entry = json.loads(raw[0])
    assert entry["reason"] == "dispatcher_starved_escalation"
    assert entry["exit_code"] == 78
    assert entry["pid"] == os.getpid()
    assert isinstance(entry["ts"], int)


def test_startup_cause_after_launchd_exit_78(tmp_runtime_dir: Path) -> None:
    """После escalation запись → cause `previous_exit_via_launchd_exit_78`."""
    from src.core.restart_cause import record_exit_intent, record_startup_cause

    # Имитируем предыдущий процесс: записал intent через _launchd_exit_78.
    record_exit_intent("dispatcher_starved_escalation", exit_code=78)
    # И last_seen_pid от предыдущего процесса (отличается от текущего).
    (tmp_runtime_dir / "krab_last_seen_pid").write_text("99999", encoding="utf-8")

    payload = record_startup_cause()
    assert payload["cause"] == "previous_exit_via_launchd_exit_78"
    assert payload["previous_reason"] == "dispatcher_starved_escalation"
    assert payload["previous_pid"] == 99999
    assert payload["current_pid"] == os.getpid()


def test_startup_cause_manual_restart_cold_start(tmp_runtime_dir: Path) -> None:
    """Нет ни history, ни last_seen_pid → `cold_first_start`."""
    from src.core.restart_cause import record_startup_cause

    payload = record_startup_cause()
    assert payload["cause"] == "cold_first_start"
    assert payload["previous_reason"] is None
    assert payload["previous_pid"] is None
    # После вызова last_seen_pid должен появиться.
    assert (tmp_runtime_dir / "krab_last_seen_pid").exists()


def test_startup_cause_after_graceful_shutdown(tmp_runtime_dir: Path) -> None:
    """graceful_sigterm intent → cause `previous_clean_shutdown`."""
    from src.core.restart_cause import record_exit_intent, record_startup_cause

    record_exit_intent("graceful_sigterm", exit_code=0)
    (tmp_runtime_dir / "krab_last_seen_pid").write_text("12345", encoding="utf-8")

    payload = record_startup_cause()
    assert payload["cause"] == "previous_clean_shutdown"
    assert payload["previous_reason"] == "graceful_sigterm"


def test_startup_cause_after_crash_no_intent(tmp_runtime_dir: Path) -> None:
    """last_seen_pid отличается от текущего, но нет intent → `previous_crash_or_kill`."""
    from src.core.restart_cause import record_startup_cause

    (tmp_runtime_dir / "krab_last_seen_pid").write_text("55555", encoding="utf-8")
    # exit_history.jsonl отсутствует → нет intent.

    payload = record_startup_cause()
    assert payload["cause"] == "previous_crash_or_kill"
    assert payload["previous_pid"] == 55555


def test_startup_cause_records_startup_marker(tmp_runtime_dir: Path) -> None:
    """record_startup_cause пишет `startup_recorded` запись в history."""
    from src.core.restart_cause import record_startup_cause

    record_startup_cause()
    path = tmp_runtime_dir / "krab_exit_history.jsonl"
    assert path.exists()
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    # Минимум одна запись startup_recorded.
    startup_entries = [e for e in entries if e.get("reason") == "startup_recorded"]
    assert len(startup_entries) == 1
    assert startup_entries[0]["pid"] == os.getpid()


def test_history_rotation_truncates(tmp_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """При превышении MAX_LINES хвост обрезается до KEEP_LINES."""
    monkeypatch.setenv("KRAB_EXIT_HISTORY_MAX_LINES", "5")
    monkeypatch.setenv("KRAB_EXIT_HISTORY_KEEP_LINES", "3")
    # Re-import т.к. _HISTORY_MAX_LINES читается на import-time.
    import importlib

    import src.core.restart_cause as rc

    importlib.reload(rc)

    for i in range(10):
        rc.record_exit_intent(f"event_{i}", exit_code=i)

    path = tmp_runtime_dir / "krab_exit_history.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    # После последней записи rotation должен сработать и оставить ≤ KEEP_LINES + 1.
    assert len(lines) <= 5, f"ожидали ≤ 5 строк после rotation, получили {len(lines)}"
    # Самая последняя запись должна быть event_9.
    last = json.loads(lines[-1])
    assert last["reason"] == "event_9"


def test_record_exit_intent_fail_open_on_io_error(tmp_runtime_dir: Path) -> None:
    """Если запись падает — функция не должна raise (мы умираем)."""
    from src.core.restart_cause import record_exit_intent

    # Подсовываем невалидный путь через env.
    with patch("src.core.restart_cause._exit_history_path") as mock_path:
        mock_path.side_effect = RuntimeError("simulated")
        # Не должно raise.
        record_exit_intent("test_reason", exit_code=1)


def test_launchd_exit_78_records_intent(tmp_runtime_dir: Path) -> None:
    """`_launchd_exit_78` пишет intent в history ПЕРЕД SystemExit."""
    # В pytest-окружении _launchd_exit_78 raises SystemExit(78) вместо os._exit.
    from src.userbot.network_watchdog import _launchd_exit_78

    with pytest.raises(SystemExit) as exc_info:
        _launchd_exit_78("test_escalation_reason")

    assert exc_info.value.code == 78
    # И запись в history должна быть.
    path = tmp_runtime_dir / "krab_exit_history.jsonl"
    assert path.exists(), "_launchd_exit_78 должен был записать exit intent"
    entries = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    intent_entries = [e for e in entries if e.get("reason") == "test_escalation_reason"]
    assert len(intent_entries) == 1
    assert intent_entries[0]["exit_code"] == 78
