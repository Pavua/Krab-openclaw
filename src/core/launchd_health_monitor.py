# -*- coding: utf-8 -*-
"""
Wave 75: health monitor для всех ai.krab.* LaunchAgents.

Periodic snapshot launchctl-state каждые 5 минут → Prometheus gauges.

Метрики (экспонируются через src.core.prometheus_metrics.collect_metrics):
    krab_launchd_last_exit_status{label}  — последний exit code (0=success)
    krab_launchd_running{label}            — 1 если PID present, 0 иначе

Alert `LaunchAgentFailed`: `krab_launchd_last_exit_status > 0` for 10m
(skipped: 0/-9/-15 — нормальные термальные сигналы при rotation).

Pattern: fits existing lm_studio_idle_watcher / chat_ban_cache periodic_cleanup —
in-process asyncio task, snapshot хранится в module-level dict, читается on-scrape
из collect_metrics().
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import traceback
from typing import Callable

import structlog

from src.core.subprocess_env import clean_subprocess_env

logger = structlog.get_logger(__name__)

# Префикс для фильтрации launchctl list. ai.openclaw.gateway и com.krab.mcp-*
# тоже включаем — это часть Krab-экосистемы по факту.
_TRACKED_PREFIXES: tuple[str, ...] = ("ai.krab.", "ai.openclaw.", "com.krab.")

# Период snapshot (сек). 300 = 5 минут.
_CHECK_INTERVAL_SEC: int = 300

# Snapshot структура: label → {"pid": int | None, "exit_status": int, "ts": float}
# Module-level, читается из collect_metrics() on-scrape.
_SNAPSHOT: dict[str, dict[str, object]] = {}
_LAST_SNAPSHOT_TS: list[float] = [0.0]


def parse_launchctl_output(output: str) -> list[tuple[str | None, int, str]]:
    """Парсит вывод `launchctl list` → [(pid, status, label), ...].

    Формат launchctl list:
        PID    Status  Label
        -      0       com.apple.foo
        12345  0       ai.krab.core
        -      1       ai.krab.broken

    pid="-" → None (не запущен). Невалидные строки тихо пропускаются.
    """
    rows: list[tuple[str | None, int, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("PID"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_raw, status_raw, label = parts[0], parts[1], parts[2].strip()
        if not label:
            continue
        pid: str | None
        if pid_raw == "-":
            pid = None
        else:
            try:
                # Validate that PID is integer (но храним как строку для compat).
                int(pid_raw)
                pid = pid_raw
            except ValueError:
                pid = None
        try:
            status = int(status_raw)
        except ValueError:
            continue
        rows.append((pid, status, label))
    return rows


def filter_krab_agents(
    rows: list[tuple[str | None, int, str]],
    prefixes: tuple[str, ...] = _TRACKED_PREFIXES,
) -> list[tuple[str | None, int, str]]:
    """Оставляет только LaunchAgents с одним из tracked-префиксов."""
    return [row for row in rows if any(row[2].startswith(p) for p in prefixes)]


def build_snapshot(
    rows: list[tuple[str | None, int, str]],
    *,
    now: float | None = None,
) -> dict[str, dict[str, object]]:
    """Строит snapshot из отфильтрованных строк launchctl."""
    ts = now if now is not None else time.time()
    snap: dict[str, dict[str, object]] = {}
    for pid, status, label in rows:
        snap[label] = {
            "pid": pid,
            "exit_status": status,
            "ts": ts,
        }
    return snap


def _run_launchctl_list() -> str:
    """Запускает `launchctl list` и возвращает stdout (или пустую строку при ошибке)."""
    try:
        result = subprocess.run(
            ["/bin/launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            env=clean_subprocess_env(),
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "launchd_health_launchctl_nonzero",
                returncode=result.returncode,
                stderr=(result.stderr or "")[:200],
            )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(
            "launchd_health_launchctl_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ""


def refresh_snapshot_sync(
    *,
    runner: Callable[[], str] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, dict[str, object]]:
    """Одна итерация: запускает launchctl, парсит, обновляет module-level snapshot.

    runner/now_fn — для инъекции в тестах. Возвращает копию нового snapshot.
    """
    global _SNAPSHOT
    output = (runner or _run_launchctl_list)()
    rows = filter_krab_agents(parse_launchctl_output(output))
    now = (now_fn or time.time)()
    snap = build_snapshot(rows, now=now)
    _SNAPSHOT = snap
    _LAST_SNAPSHOT_TS[0] = now
    return dict(snap)


def get_snapshot() -> dict[str, dict[str, object]]:
    """Возвращает копию текущего snapshot (для prometheus_metrics.collect_metrics)."""
    return {label: dict(data) for label, data in _SNAPSHOT.items()}


def get_last_snapshot_ts() -> float:
    """Unix ts последнего successful snapshot (0.0 если ни разу не запускались)."""
    return _LAST_SNAPSHOT_TS[0]


class LaunchdHealthMonitor:
    """Background asyncio task: каждые 5 минут refresh_snapshot_sync()."""

    def __init__(
        self,
        *,
        interval_sec: int = _CHECK_INTERVAL_SEC,
        runner: Callable[[], str] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._interval = max(30, int(interval_sec))
        self._runner = runner
        self._now_fn = now_fn
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Запускает background loop (идемпотентен)."""
        if self._task is not None and not self._task.done():
            return
        # Первый snapshot — сразу, чтобы /metrics не отдавал пустоту.
        try:
            refresh_snapshot_sync(runner=self._runner, now_fn=self._now_fn)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "launchd_health_initial_snapshot_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        self._task = asyncio.create_task(self._loop(), name="launchd_health_monitor")
        logger.info("launchd_health_monitor_started", interval_sec=self._interval)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                # launchctl list ~50ms, но на всякий случай — to_thread.
                await asyncio.to_thread(
                    refresh_snapshot_sync,
                    runner=self._runner,
                    now_fn=self._now_fn,
                )
            except asyncio.CancelledError:
                logger.info("launchd_health_monitor_stopped")
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "launchd_health_monitor_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )


# Module-level singleton.
launchd_health_monitor = LaunchdHealthMonitor()
