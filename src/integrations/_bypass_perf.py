"""Wave 31-A: bypass latency profiler.

Каждый bypass call → запись в `~/.openclaw/krab_runtime_state/bypass_perf.jsonl`:
  {ts, kind, model, duration_sec, success, response_len, error_type}

kind значения: 'cli', 'vertex', 'anthropic-vertex', 'google-direct', 'gemma'
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Путь к JSONL-файлу с записями latency
PERF_LOG = Path.home() / ".openclaw/krab_runtime_state/bypass_perf.jsonl"


def record_bypass_call(
    *,
    kind: str,
    model: str,
    duration_sec: float,
    success: bool,
    response_len: int = 0,
    error_type: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Записывает событие bypass call в JSONL. Graceful: ошибки молча подавляются."""
    try:
        PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "ts": time.time(),
            "kind": kind,
            "model": model,
            "duration_sec": round(duration_sec, 3),
            "success": success,
            "response_len": response_len,
            "error_type": error_type,
            **(extra or {}),
        }
        with PERF_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001
        pass  # никогда не крашим bypass из-за профилировщика


def aggregate_perf(window_sec: int = 3600) -> dict[str, Any]:
    """Читает bypass_perf.jsonl, возвращает агрегированную статистику per kind+model.

    Args:
        window_sec: окно в секундах (default 3600 = 1h).

    Returns::

        {
          "total_calls": int,
          "total_failures": int,
          "window_sec": int,
          "by_kind": {
            "cli": {"count": int, "p50": float, "p95": float, "p99": float,
                    "mean": float, "fail_rate": float},
            ...
          },
          "by_model": {
            "codex-cli/gpt-5.5": {"count": int, "p50": float, ...}
          }
        }
    """
    if not PERF_LOG.exists():
        return {
            "total_calls": 0,
            "total_failures": 0,
            "window_sec": window_sec,
            "by_kind": {},
            "by_model": {},
        }

    cutoff = time.time() - window_sec
    records: list[dict[str, Any]] = []
    try:
        with PERF_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("ts", 0) >= cutoff:
                        records.append(r)
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return {
            "total_calls": 0,
            "total_failures": 0,
            "window_sec": window_sec,
            "by_kind": {},
            "by_model": {},
        }

    def _percentile(values: list[float], pct: int) -> float:
        """Вычисляет перцентиль списка значений."""
        if not values:
            return 0.0
        s = sorted(values)
        idx = min(int(len(s) * pct / 100), len(s) - 1)
        return round(s[idx], 3)

    def _stats_for(subset: list[dict[str, Any]]) -> dict[str, Any]:
        """Агрегирует статистику по подмножеству записей."""
        durs = [r["duration_sec"] for r in subset if "duration_sec" in r]
        fails = [r for r in subset if not r.get("success", True)]
        count = len(subset)
        return {
            "count": count,
            "fail_rate": round(len(fails) / max(count, 1), 3),
            "p50": _percentile(durs, 50),
            "p95": _percentile(durs, 95),
            "p99": _percentile(durs, 99),
            "mean": round(sum(durs) / max(len(durs), 1), 3),
        }

    # Группируем по kind и model
    by_kind: dict[str, list[dict[str, Any]]] = {}
    by_model: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_kind.setdefault(r.get("kind", "unknown"), []).append(r)
        by_model.setdefault(r.get("model", "unknown"), []).append(r)

    total_failures = len([r for r in records if not r.get("success", True)])

    return {
        "total_calls": len(records),
        "total_failures": total_failures,
        "window_sec": window_sec,
        "by_kind": {k: _stats_for(v) for k, v in by_kind.items()},
        "by_model": {k: _stats_for(v) for k, v in by_model.items()},
    }


def parse_duration(window: str) -> int:
    """Парсит строку window ('1h', '24h', '5m', '30s', '3600') в секунды.

    Args:
        window: строка вида '1h', '24h', '5m', '90s' или чистое число секунд.

    Returns:
        Количество секунд (int). Default 3600 при невалидном вводе.
    """
    window = window.strip().lower()
    try:
        if window.endswith("h"):
            return int(window[:-1]) * 3600
        if window.endswith("m"):
            return int(window[:-1]) * 60
        if window.endswith("s"):
            return int(window[:-1])
        return int(window)
    except (ValueError, IndexError):
        return 3600  # fallback: 1 час
