# -*- coding: utf-8 -*-
"""
Idle-watcher для LM Studio: автоматически выгружает модель после N секунд простоя.

Root-fix для CPU starvation (load avg 73+): llmworker держит 57% CPU
даже когда модель не используется. Выгрузка освобождает RAM и CPU.

Env-переменные:
    LM_STUDIO_IDLE_UNLOAD_ENABLED  — включить (1/true/yes; default=1)
    LM_STUDIO_IDLE_UNLOAD_SEC      — порог простоя в секундах (default=600)

Prometheus-counter: krab_lm_studio_idle_unloads_total (in-process MetricsRegistry).
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from typing import TYPE_CHECKING, Callable

import structlog

if TYPE_CHECKING:
    from src.model_manager import ModelManager

logger = structlog.get_logger(__name__)

# Интервал проверки простоя (сек)
_CHECK_INTERVAL_SEC: int = 60

# Prometheus-подобный счётчик выгрузок (in-process)
_idle_unloads_total: int = 0


def get_idle_unloads_total() -> int:
    """Возвращает общее число idle-выгрузок с момента старта."""
    return _idle_unloads_total


def _is_enabled() -> bool:
    """Проверяет env-флаг LM_STUDIO_IDLE_UNLOAD_ENABLED (default=1)."""
    raw = os.environ.get("LM_STUDIO_IDLE_UNLOAD_ENABLED", "1").strip().lower()
    return raw in ("1", "true", "yes")


def _get_threshold_sec() -> float:
    """Возвращает LM_STUDIO_IDLE_UNLOAD_SEC (default=600)."""
    try:
        return float(os.environ.get("LM_STUDIO_IDLE_UNLOAD_SEC", "600"))
    except (TypeError, ValueError):
        return 600.0


class LmStudioIdleWatcher:
    """
    Фоновый asyncio-task: выгружает LM Studio модель после N секунд простоя.

    Логика намеренно легковесная — не дублирует guarded-unload из maintenance_loop,
    а добавляет независимый env-gate и отдельный Prometheus counter.
    Использует _last_any_activity_ts из ModelManager для определения простоя.
    """

    def __init__(
        self,
        model_manager: "ModelManager",
        *,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._mm = model_manager
        # Инъекция часов для тестов; по умолчанию time.time()
        self._now: Callable[[], float] = now_fn or time.time
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Запускает background task (идемпотентен — повторный вызов ignored)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="lm_studio_idle_watcher")

    def stop(self) -> None:
        """Останавливает background task."""
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        logger.info("lm_studio_idle_watcher_started", interval_sec=_CHECK_INTERVAL_SEC)
        while True:
            try:
                await asyncio.sleep(_CHECK_INTERVAL_SEC)
                await self._check_once()
            except asyncio.CancelledError:
                logger.info("lm_studio_idle_watcher_stopped")
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "lm_studio_idle_watcher_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )

    async def _check_once(self) -> None:
        """Одна итерация проверки простоя."""
        if not _is_enabled():
            return

        mm = self._mm
        # Нет загруженной модели — выгружать нечего
        if not mm._current_model:
            return

        threshold = _get_threshold_sec()
        now = self._now()

        # Используем _last_any_activity_ts из ModelManager
        last_activity = float(getattr(mm, "_last_any_activity_ts", 0.0) or 0.0)
        elapsed = now - last_activity

        if elapsed < threshold:
            return

        # Есть активные запросы — пропускаем
        active = int(getattr(mm, "_active_requests", 0) or 0)
        if active > 0:
            logger.info(
                "lm_studio_idle_watcher_skip_active",
                model=mm._current_model,
                active_requests=active,
                elapsed_sec=round(elapsed, 1),
            )
            return

        model_name = mm._current_model
        logger.info(
            "lm_studio_idle_unload_triggered",
            model=model_name,
            elapsed_sec=round(elapsed, 1),
            threshold_sec=threshold,
        )

        try:
            await mm.unload_all()
            global _idle_unloads_total
            _idle_unloads_total += 1
            logger.info(
                "lm_studio_idle_unload_done",
                model=model_name,
                krab_lm_studio_idle_unloads_total=_idle_unloads_total,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "lm_studio_idle_unload_failed",
                model=model_name,
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )


# Singleton — инициализируется при старте userbot через configure()
_watcher: LmStudioIdleWatcher | None = None


def configure(model_manager: "ModelManager") -> LmStudioIdleWatcher:
    """
    Инициализирует и запускает singleton watcher.
    Вызывается из KraabUserbot.start() после инициализации model_manager.
    """
    global _watcher
    if _watcher is not None:
        _watcher.stop()
    _watcher = LmStudioIdleWatcher(model_manager)
    _watcher.start()
    return _watcher


def get_watcher() -> LmStudioIdleWatcher | None:
    """Возвращает текущий singleton watcher (None если не инициализирован)."""
    return _watcher
