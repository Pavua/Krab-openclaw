# -*- coding: utf-8 -*-
"""
restart_cause — отслеживание ПРИЧИНЫ перезапуска Краба для ops-видимости.

Назначение (S64 Wave 4):
Сейчас мы не видим легко, ПОЧЕМУ Krab перезапустился:
  - Manual через `launchctl kickstart`?
  - Auto через launchd respawn (silent-death escalation `_launchd_exit_78`)?
  - Auto через crash (Sentry ловит)?
  - Owner через start_krab.command?

Этот модуль:
1. На старте Krab читает историю exit events (`krab_exit_history.jsonl`),
   сопоставляет с `krab_cold_starts.log` + `krab_main.exit` файлом и
   логирует structured event `krab_startup_cause` с лучшим guess.
2. На graceful exit или escalation путях (`_launchd_exit_78`, и т.п.)
   записывает intent в `krab_exit_history.jsonl` через `record_exit_intent()`.

Format `krab_exit_history.jsonl` (1 строка = 1 событие):
    {"ts": 1779054246, "pid": 12345, "exit_code": 78,
     "reason": "dispatcher_starved_escalation"}

Возможные значения `cause` для `krab_startup_cause`:
  - `previous_exit_via_launchd_exit_78` — предыдущий процесс escalated через 78
  - `previous_clean_shutdown`           — graceful SIGTERM/SIGINT
  - `previous_crash_or_kill`            — нет intent записи + есть PID change
  - `cold_first_start`                  — нет истории, чистый старт
  - `restart_after_pid_change`          — PID отличается от last_seen но без intent
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Где живёт runtime state. Совпадает с RUNTIME_STATE_DIR в launcher'е.
def _runtime_state_dir() -> Path:
    env_dir = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _exit_history_path() -> Path:
    return _runtime_state_dir() / "krab_exit_history.jsonl"


def _last_seen_pid_path() -> Path:
    return _runtime_state_dir() / "krab_last_seen_pid"


def _krab_main_exit_path() -> Path:
    # Файл записывается launcher'ом `new start_krab.command` после wait/PID exit.
    return _runtime_state_dir() / "krab_main.exit"


# Максимум строк в jsonl, при превышении хвост обрезается.
_HISTORY_MAX_LINES = int(os.environ.get("KRAB_EXIT_HISTORY_MAX_LINES", "500"))
_HISTORY_KEEP_LINES = int(os.environ.get("KRAB_EXIT_HISTORY_KEEP_LINES", "250"))


def record_exit_intent(reason: str, *, exit_code: int | None = None) -> None:
    """Записывает намерение завершения процесса в jsonl.

    Вызывается ДО `os._exit`/`sys.exit`, чтобы следующий старт мог установить
    причину рестарта. Fail-open: любая ошибка проглатывается (мы уже умираем).

    Args:
        reason:     symbolic reason, e.g. "dispatcher_starved_escalation",
                    "graceful_sigterm", "db_corruption", "manual_stop".
        exit_code:  опциональный exit code (например, 78 для EX_CONFIG).
    """
    try:
        path = _exit_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts": int(time.time()),
            "pid": os.getpid(),
            "exit_code": int(exit_code) if exit_code is not None else None,
            "reason": str(reason or "unknown"),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        _rotate_history_if_needed(path)
    except Exception as exc:  # noqa: BLE001 — fail-open
        try:
            logger.warning("exit_intent_record_failed", error=str(exc), reason=reason)
        except Exception:  # noqa: BLE001
            pass


def _rotate_history_if_needed(path: Path) -> None:
    """Простой tail-rotation: при > MAX_LINES оставляем KEEP_LINES последних."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= _HISTORY_MAX_LINES:
            return
        tail = lines[-_HISTORY_KEEP_LINES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(tail), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — best-effort, log skipped
        pass


def _read_last_exit_entries(limit: int = 5) -> list[dict[str, Any]]:
    """Читает последние N записей из jsonl, samый свежий — последний."""
    path = _exit_history_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        out: list[dict[str, Any]] = []
        for raw in lines[-limit:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:  # noqa: BLE001 — skip malformed
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


def _read_last_seen_pid() -> int | None:
    path = _last_seen_pid_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _write_last_seen_pid(pid: int) -> None:
    path = _last_seen_pid_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(str(int(pid)), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass


def _read_previous_main_exit_code() -> int | None:
    """Читает `krab_main.exit` — exit code предыдущего процесса (пишется launcher'ом)."""
    path = _krab_main_exit_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        # Bash может писать "-9" (SIGKILL → exit_status в bash) или "0"/"78"/etc.
        return int(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _count_recent_cold_starts(window_sec: int = 300) -> int:
    """Сколько cold-starts (из `krab_cold_starts.log`) попало в окно."""
    log_path = _runtime_state_dir() / "krab_cold_starts.log"
    if not log_path.exists():
        return 0
    try:
        now = int(time.time())
        cutoff = now - window_sec
        count = 0
        with log_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ts = int(raw)
                except Exception:  # noqa: BLE001
                    continue
                if ts >= cutoff:
                    count += 1
        return count
    except Exception:  # noqa: BLE001
        return 0


def record_startup_cause() -> dict[str, Any]:
    """Логирует structured event `krab_startup_cause` с лучшим guess.

    Вызывается один раз на старте, до основной работы. Возвращает dict
    с тем, что было залогировано (для тестов/диагностики).

    Алгоритм:
      1. читаем `krab_last_seen_pid`
      2. читаем `krab_main.exit` (exit code предыдущего процесса)
      3. читаем последние записи из `krab_exit_history.jsonl`
      4. определяем cause + previous_uptime_sec (если возможно)
      5. логируем `krab_startup_cause`
      6. перезаписываем `krab_last_seen_pid` текущим PID
    """
    current_pid = os.getpid()
    last_seen_pid = _read_last_seen_pid()
    previous_exit_code = _read_previous_main_exit_code()
    history = _read_last_exit_entries(limit=5)

    last_intent: dict[str, Any] | None = history[-1] if history else None
    cause = "cold_first_start"
    previous_reason: str | None = None
    previous_uptime_sec: int | None = None

    if last_intent is not None:
        previous_reason = str(last_intent.get("reason") or "")
        intent_exit_code = last_intent.get("exit_code")
        # Match by reason / exit_code.
        if intent_exit_code == 78 or previous_reason.endswith("_escalation"):
            cause = "previous_exit_via_launchd_exit_78"
        elif previous_reason in {"graceful_sigterm", "graceful_sigint", "graceful_stop"}:
            cause = "previous_clean_shutdown"
        elif previous_reason == "db_corruption":
            cause = "previous_db_corruption_quarantine"
        else:
            cause = "previous_intent_logged"
        # Восстанавливаем uptime если в истории есть startup entry или из PID.
        intent_ts = last_intent.get("ts")
        if isinstance(intent_ts, (int, float)):
            # Если есть предыдущий startup_recorded event — используем его, иначе skip.
            for past in reversed(history[:-1]):
                if past.get("reason") == "startup_recorded":
                    start_ts = past.get("ts")
                    if isinstance(start_ts, (int, float)):
                        previous_uptime_sec = max(0, int(intent_ts - start_ts))
                        break
    elif last_seen_pid is not None and last_seen_pid != current_pid:
        # Был предыдущий процесс, но никаких intent записей — крэш / kill -9.
        cause = "previous_crash_or_kill"
    elif last_seen_pid == current_pid:
        # Тот же PID. Редкий случай: in-process retry (`_run_with_retry`).
        cause = "in_process_retry"

    recent_cold_starts = _count_recent_cold_starts(window_sec=300)

    log_payload: dict[str, Any] = {
        "cause": cause,
        "previous_pid": last_seen_pid,
        "current_pid": current_pid,
        "previous_exit_code": previous_exit_code,
        "previous_reason": previous_reason,
        "previous_uptime_sec": previous_uptime_sec,
        "recent_cold_starts_5min": recent_cold_starts,
    }
    try:
        logger.info("krab_startup_cause", **log_payload)
    except Exception:  # noqa: BLE001 — не блокируем boot
        pass

    # Пишем startup-marker в exit_history — чтобы следующая итерация смогла
    # вычислить uptime предыдущего процесса.
    try:
        path = _exit_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": int(time.time()),
            "pid": current_pid,
            "exit_code": None,
            "reason": "startup_recorded",
            "cause": cause,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        _rotate_history_if_needed(path)
    except Exception:  # noqa: BLE001
        pass

    _write_last_seen_pid(current_pid)
    return log_payload


__all__ = ["record_exit_intent", "record_startup_cause"]
