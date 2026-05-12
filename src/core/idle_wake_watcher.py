# -*- coding: utf-8 -*-
"""
src/core/idle_wake_watcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 119: macOS idle/wake detection — комплементарный observability-слой
поверх Wave 36-D (`_macos_sleep_detect_loop`).

Алгоритм:
  - Каждые `interval_sec` секунд асинхронный loop делает `asyncio.sleep`.
  - Сравниваем фактический delta (`time.monotonic()`) с ожидаемым.
  - Если delta > `threshold_sec` — event loop был приостановлен
    (вероятно, macOS sleep) → emit `idle_wake_detected`.

Отличие от Wave 36-D: не дёргает Pyrofork reinit напрямую, а вызывает
произвольный callback (Krab может зацепить session checkpoint /
OAuth refresh / Pyrogram reconnect). Loop полностью stateless и
используется как observability + extension point.

ENV:
  KRAB_IDLE_WAKE_WATCHER_ENABLED   — 1/true/yes (default=1)
  KRAB_IDLE_WAKE_INTERVAL_SEC      — интервал check'а (default=30)
  KRAB_IDLE_WAKE_THRESHOLD_SEC     — порог wake-детекта (default=120)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Awaitable, Callable

from .logger import get_logger
from .metrics.idle_wake import record_idle_wake

logger = get_logger(__name__)


# Тип callback: принимает gap_seconds (float), возвращает coroutine или None.
WakeCallback = Callable[[float], Awaitable[None] | None]


def _env_enabled(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes"}


def _env_float(name: str, default: float) -> float:
    """Чтение env с fallback на default при пустых/некорректных значениях."""
    try:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


async def idle_wake_watcher_loop(
    *,
    on_wake: WakeCallback | None = None,
    interval_sec: float | None = None,
    threshold_sec: float | None = None,
    enabled: bool | None = None,
    _monotonic: Callable[[], float] = time.monotonic,
    _wall_clock: Callable[[], float] = time.time,
) -> None:
    """Фоновый loop детекта idle/wake.

    Параметры (для тестов) переопределяют env-переменные.

    on_wake: optional callback (sync or async), вызывается при детекте
    wake-события. Получает gap_seconds. Исключения внутри callback'а
    логируются как WARNING и не ломают loop.
    """
    if enabled is None:
        enabled = _env_enabled("KRAB_IDLE_WAKE_WATCHER_ENABLED", "1")
    if not enabled:
        logger.info("idle_wake_watcher_disabled")
        return

    _interval = (
        interval_sec
        if interval_sec is not None
        else _env_float("KRAB_IDLE_WAKE_INTERVAL_SEC", 30.0)
    )
    _threshold = (
        threshold_sec
        if threshold_sec is not None
        else _env_float("KRAB_IDLE_WAKE_THRESHOLD_SEC", 120.0)
    )

    logger.info(
        "idle_wake_watcher_started",
        interval_sec=_interval,
        threshold_sec=_threshold,
    )

    last_check = _monotonic()
    while True:
        try:
            await asyncio.sleep(_interval)
        except asyncio.CancelledError:
            logger.info("idle_wake_watcher_cancelled")
            break

        now = _monotonic()
        delta = now - last_check
        last_check = now

        if delta > _threshold:
            gap = float(delta)
            wall_ts = float(_wall_clock())
            logger.warning(
                "idle_wake_detected",
                gap_seconds=round(gap, 1),
                expected_interval_sec=_interval,
                threshold_sec=_threshold,
            )
            try:
                record_idle_wake(gap, wall_ts)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "idle_wake_metrics_failed",
                    error=str(exc)[:200],
                    error_type=type(exc).__name__,
                )

            if on_wake is not None:
                try:
                    result = on_wake(gap)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "idle_wake_callback_failed",
                        error=str(exc)[:200],
                        error_type=type(exc).__name__,
                    )
