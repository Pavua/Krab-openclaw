# -*- coding: utf-8 -*-
"""Wave 111: монитор свободного места на диске.

Периодически снимает snapshot через ``shutil.disk_usage`` для набора путей и
обновляет Prometheus gauge'ы. Если путь отсутствует — graceful skip.
Background asyncio loop запускается из bootstrap; интервал управляется
переменной окружения ``KRAB_DISK_CHECK_INTERVAL_SEC`` (по умолчанию 600).

Цель — заранее предупреждать о приближающемся переполнении диска: при <10%
свободного места возможна SQLite-corruption и потеря сообщений.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from typing import Callable, Iterable

from .logger import get_logger
from .metrics.disk_space import record_disk_usage

logger = get_logger(__name__)


# Пути по умолчанию: home владельца, корень FS и рабочее дерево Krab.
DEFAULT_PATHS: tuple[str, ...] = (
    "/Users/pablito",
    "/",
    "/Users/pablito/Antigravity_AGENTS/Краб",
)

DEFAULT_INTERVAL_SEC = 600

# Пороги для logger.warning (Prometheus alerts отдельно — krab_alerts.yml).
WARN_USED_PCT = 90.0
CRITICAL_USED_PCT = 95.0


@dataclass(frozen=True)
class DiskSnapshot:
    """Снимок состояния одной точки монтирования."""

    mount: str
    total: int
    used: int
    free: int
    used_pct: float
    available: bool  # False если путь отсутствует/ошибка


def _compute_used_pct(total: int, free: int) -> float:
    """Возвращает % использования; для total<=0 возвращает 0.0."""
    if total <= 0:
        return 0.0
    used = max(0, total - free)
    return round((used / total) * 100.0, 2)


def take_snapshot(path: str) -> DiskSnapshot:
    """Снимает disk usage для одного пути. Возвращает available=False при ошибке."""
    try:
        usage = shutil.disk_usage(path)
    except (FileNotFoundError, OSError, PermissionError) as exc:
        logger.warning(
            "disk_space_snapshot_failed",
            mount=path,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return DiskSnapshot(mount=path, total=0, used=0, free=0, used_pct=0.0, available=False)

    used_pct = _compute_used_pct(usage.total, usage.free)
    return DiskSnapshot(
        mount=path,
        total=int(usage.total),
        used=int(usage.used),
        free=int(usage.free),
        used_pct=used_pct,
        available=True,
    )


def collect_snapshots(paths: Iterable[str] | None = None) -> list[DiskSnapshot]:
    """Снимает snapshot по всем путям и обновляет Prometheus gauge'ы."""
    targets = tuple(paths) if paths is not None else DEFAULT_PATHS
    snapshots: list[DiskSnapshot] = []
    for path in targets:
        snap = take_snapshot(path)
        snapshots.append(snap)
        if not snap.available:
            continue
        record_disk_usage(mount=snap.mount, free_bytes=snap.free, used_pct=snap.used_pct)
        if snap.used_pct >= CRITICAL_USED_PCT:
            logger.error(
                "disk_space_critical",
                mount=snap.mount,
                used_pct=snap.used_pct,
                free_bytes=snap.free,
            )
        elif snap.used_pct >= WARN_USED_PCT:
            logger.warning(
                "disk_space_low",
                mount=snap.mount,
                used_pct=snap.used_pct,
                free_bytes=snap.free,
            )
        else:
            logger.debug(
                "disk_space_ok",
                mount=snap.mount,
                used_pct=snap.used_pct,
                free_bytes=snap.free,
            )
    return snapshots


def _resolve_interval(default: int = DEFAULT_INTERVAL_SEC) -> int:
    """Читает KRAB_DISK_CHECK_INTERVAL_SEC; невалидное → default."""
    raw = os.environ.get("KRAB_DISK_CHECK_INTERVAL_SEC")
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


async def disk_space_monitor_loop(
    paths: Iterable[str] | None = None,
    *,
    interval_sec: int | None = None,
    sleeper: Callable[[float], "asyncio.Future[None]"] | None = None,
) -> None:
    """Фоновый loop: собирает snapshot, спит, повторяет.

    ``sleeper`` инжектируется в тестах, чтобы избежать реального ``asyncio.sleep``.
    Выход — по ``asyncio.CancelledError``.
    """
    interval = interval_sec if interval_sec is not None else _resolve_interval()
    targets = tuple(paths) if paths is not None else DEFAULT_PATHS
    sleep_fn = sleeper if sleeper is not None else asyncio.sleep
    logger.info(
        "disk_space_monitor_started",
        interval_sec=interval,
        mounts=list(targets),
    )
    try:
        while True:
            try:
                collect_snapshots(targets)
            except Exception as exc:  # noqa: BLE001 — never break loop
                logger.warning(
                    "disk_space_monitor_iteration_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            await sleep_fn(interval)
    except asyncio.CancelledError:
        logger.info("disk_space_monitor_stopped")
        raise
