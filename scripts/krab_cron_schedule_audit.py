#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 88: cron schedule consistency audit для всех ai.krab.*.plist LaunchAgents.

Цель: обнаружить «stale_cron» — задачи, у которых declared schedule
(StartCalendarInterval/StartInterval) не подтверждается mtime log-файла.
Wave 75 LaunchAgent health monitor показывает exit codes/running state,
но не отслеживает «должно было выполниться, а лог не trogan уже 3 дня».

Алгоритм:
1. Обойти ~/Library/LaunchAgents/ai.krab.*.plist.
2. Распарсить каждый через `plutil -convert json -o - <plist>`.
3. Извлечь schedule:
   - StartCalendarInterval (Weekday/Hour/Minute/Day/Month) → cron-style;
   - StartInterval (seconds) → fixed interval;
   - иначе → schedule_type="none" (RunAtLoad only / KeepAlive only — пропускаем).
4. Вычислить expected_interval_sec — оценка «как часто должен запускаться».
5. Найти log: StandardOutPath (или logs/<label>.out.log в корне Krab).
6. Сравнить mtime(log) с now. Если actual_age_sec > 2 × expected_interval_sec
   → stale_cron.
7. Output JSON snapshot в stdout + persist в
   ~/.openclaw/krab_runtime_state/cron_schedule_audit.json.

Usage:
    venv/bin/python scripts/krab_cron_schedule_audit.py
    venv/bin/python scripts/krab_cron_schedule_audit.py --json-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Период «expected» для cron Weekday/Hour/Minute оценок (секунды)
_SEC_PER_DAY = 86400
_SEC_PER_WEEK = 7 * _SEC_PER_DAY
_SEC_PER_HOUR = 3600

# Stale threshold — log не trogan дольше N × expected_interval_sec.
_STALE_MULTIPLIER = 2.0


def _plist_glob_dir() -> Path:
    """LaunchAgents directory для текущего пользователя."""
    return Path.home() / "Library" / "LaunchAgents"


def _runtime_state_dir() -> Path:
    """Path до openclaw runtime state."""
    return Path.home() / ".openclaw" / "krab_runtime_state"


def _krab_root() -> Path:
    """Корень репозитория Краба (для fallback logs/<label>.out.log)."""
    # Скрипт лежит в scripts/, корень — на уровень выше.
    return Path(__file__).resolve().parent.parent


def parse_plist_to_dict(plist_path: Path) -> dict[str, Any] | None:
    """Парсит plist через plutil → JSON dict. Возвращает None при ошибке."""
    try:
        proc = subprocess.run(
            ["plutil", "-convert", "json", "-o", "-", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def expected_interval_from_schedule(plist_dict: dict[str, Any]) -> tuple[str, float | None]:
    """Возвращает (schedule_type, expected_interval_sec).

    schedule_type:
      - "calendar_weekly"  — StartCalendarInterval с Weekday → ~7 дней
      - "calendar_daily"   — StartCalendarInterval с Hour/Minute (без Weekday/Day) → ~24h
      - "calendar_hourly"  — StartCalendarInterval с Minute only → ~1h
      - "calendar_other"   — нестандартный (Month/Day) → fallback ~28 дней
      - "interval"         — StartInterval (seconds) → значение поля
      - "none"             — нет cron-schedule (KeepAlive/RunAtLoad → пропускаем)
    """
    sci = plist_dict.get("StartCalendarInterval")
    si = plist_dict.get("StartInterval")

    if isinstance(si, (int, float)) and si > 0:
        return "interval", float(si)

    if isinstance(sci, dict):
        has_weekday = "Weekday" in sci
        has_day = "Day" in sci
        has_month = "Month" in sci
        has_hour = "Hour" in sci
        has_minute = "Minute" in sci

        if has_month or has_day:
            # Monthly/specific day — assume ~28 days
            return "calendar_other", 28 * _SEC_PER_DAY
        if has_weekday:
            return "calendar_weekly", _SEC_PER_WEEK
        if has_hour:
            return "calendar_daily", _SEC_PER_DAY
        if has_minute:
            return "calendar_hourly", _SEC_PER_HOUR
        # Пустой StartCalendarInterval — некорректно, считаем daily.
        return "calendar_other", _SEC_PER_DAY

    return "none", None


def resolve_log_path(plist_dict: dict[str, Any], label: str) -> Path | None:
    """Резолвит путь до stdout-лога. Fallback: <krab_root>/logs/<label>.out.log.

    Возвращает Path даже если файла нет (existence проверяется отдельно).
    """
    stdout = plist_dict.get("StandardOutPath")
    if isinstance(stdout, str) and stdout.strip():
        return Path(stdout)
    # Fallback на стандартное расположение логов Краба.
    return _krab_root() / "logs" / f"{label}.out.log"


def audit_single_plist(plist_path: Path, now_ts: float) -> dict[str, Any] | None:
    """Аудит одного plist. None если schedule отсутствует (skip)."""
    plist_dict = parse_plist_to_dict(plist_path)
    if not plist_dict:
        return None

    label = plist_dict.get("Label") or plist_path.stem
    schedule_type, expected_interval = expected_interval_from_schedule(plist_dict)

    if schedule_type == "none" or expected_interval is None:
        return None

    log_path = resolve_log_path(plist_dict, label)
    log_mtime: float | None = None
    log_exists = False
    if log_path and log_path.exists():
        try:
            log_mtime = log_path.stat().st_mtime
            log_exists = True
        except OSError:
            log_mtime = None

    actual_age_sec: float | None = None
    if log_mtime is not None:
        actual_age_sec = max(0.0, now_ts - log_mtime)

    # stale: log файл отсутствует → stale (cron не пишет stdout).
    # stale: log mtime старше 2× expected_interval.
    is_stale = False
    if not log_exists:
        is_stale = True
    elif actual_age_sec is not None and actual_age_sec > _STALE_MULTIPLIER * expected_interval:
        is_stale = True

    return {
        "label": label,
        "plist_path": str(plist_path),
        "schedule_type": schedule_type,
        "expected_interval_sec": expected_interval,
        "expected_last_run_ago_sec": expected_interval,
        "actual_last_log_mtime_ago_sec": actual_age_sec,
        "log_path": str(log_path) if log_path else None,
        "log_exists": log_exists,
        "stale_cron": is_stale,
    }


def run_audit(plist_dir: Path | None = None, now_ts: float | None = None) -> dict[str, Any]:
    """Полный обход + сборка snapshot."""
    plist_dir = plist_dir or _plist_glob_dir()
    now_ts = now_ts if now_ts is not None else time.time()

    snapshot: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "total_agents": 0,
        "scheduled_agents": 0,
        "stale_cron_count": 0,
        "stale_agents": [],
        "all_agents": [],
    }

    if not plist_dir.exists():
        return snapshot

    plists = sorted(plist_dir.glob("ai.krab.*.plist"))
    snapshot["total_agents"] = len(plists)

    for plist_path in plists:
        result = audit_single_plist(plist_path, now_ts)
        if result is None:
            continue
        snapshot["scheduled_agents"] += 1
        snapshot["all_agents"].append(result)
        if result["stale_cron"]:
            snapshot["stale_cron_count"] += 1
            snapshot["stale_agents"].append(
                {
                    "label": result["label"],
                    "schedule_type": result["schedule_type"],
                    "expected_last_run_ago_sec": result["expected_last_run_ago_sec"],
                    "actual_last_log_mtime_ago_sec": result["actual_last_log_mtime_ago_sec"],
                    "log_exists": result["log_exists"],
                }
            )

    return snapshot


def persist_snapshot(snapshot: dict[str, Any]) -> Path | None:
    """Persist в ~/.openclaw/krab_runtime_state/cron_schedule_audit.json."""
    target_dir = _runtime_state_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    target = target_dir / "cron_schedule_audit.json"
    try:
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        os.replace(tmp, target)
        return target
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Krab cron schedule audit (Wave 88)")
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print JSON snapshot only (no human-readable header).",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write snapshot to ~/.openclaw/krab_runtime_state/.",
    )
    args = parser.parse_args(argv)

    snapshot = run_audit()

    if not args.no_persist:
        persist_snapshot(snapshot)

    if args.json_only:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0

    print(
        f"[Wave 88] cron audit: total={snapshot['total_agents']} "
        f"scheduled={snapshot['scheduled_agents']} "
        f"stale={snapshot['stale_cron_count']}"
    )
    for stale in snapshot["stale_agents"]:
        actual = stale.get("actual_last_log_mtime_ago_sec")
        actual_str = f"{actual:.0f}s" if isinstance(actual, (int, float)) else "no_log"
        expected_str = f"{stale['expected_last_run_ago_sec']:.0f}s"
        print(
            f"  STALE label={stale['label']} schedule={stale['schedule_type']} "
            f"expected_interval={expected_str} actual_age={actual_str}"
        )
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
