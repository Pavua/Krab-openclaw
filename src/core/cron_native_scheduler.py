"""Native Python cron scheduler — asyncio-задача для выполнения cron_native_store jobs.

Запускается из userbot_bridge.start() и периодически (каждые 30с) опрашивает
cron_native_store, запуская просроченные jobs через AI pipeline.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Awaitable, Callable

from . import cron_native_store
from .logger import get_logger

logger = get_logger(__name__)

# Интервал опроса (секунды)
_POLL_INTERVAL = 30


class CronNativeScheduler:
    """Фоновый планировщик native cron jobs."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._sender: Callable[[str, str], Awaitable[None]] | None = None
        # Словарь job_id → timestamp последнего запуска (защита от двойного срабатывания)
        self._last_fired: dict[str, float] = {}

    def bind_sender(
        self, sender: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Привязывает callback для отправки промпта в AI pipeline."""
        self._sender = sender

    def start(self) -> None:
        """Запускает фоновую asyncio-задачу."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.ensure_future(self._loop())
        logger.info("cron_native_scheduler_started", poll_interval=_POLL_INTERVAL)

    def stop(self) -> None:
        """Останавливает фоновую задачу."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("cron_native_scheduler_stopped")
        self._task = None

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def _loop(self) -> None:
        """Основной цикл опроса."""
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "cron_native_scheduler_tick_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
            await asyncio.sleep(_POLL_INTERVAL)

    async def _tick(self) -> None:
        """Один цикл: проверяет jobs и запускает просроченные."""
        now = time.time()
        jobs = cron_native_store.list_jobs()
        for job in jobs:
            if not job.get("enabled"):
                continue
            job_id = str(job.get("id") or "")
            due_ts = cron_native_store.next_due(job)
            if due_ts is None:
                continue
            # Запускаем если next_due ≤ now + poll_interval и не было недавнего запуска
            last = self._last_fired.get(job_id, 0.0)
            if due_ts <= now + _POLL_INTERVAL and (now - last) > 50:
                self._last_fired[job_id] = now
                asyncio.ensure_future(self._run_job(job))

    async def _run_job(self, job: dict) -> None:
        """Выполняет один job: вызывает sender с промптом."""
        job_id = str(job.get("id") or "?")
        prompt = str(job.get("prompt") or "")
        if not prompt:
            return
        logger.info("cron_native_job_firing", job_id=job_id, cron_spec=job.get("cron_spec"))
        try:
            if self._sender:
                await self._sender("cron_native", prompt)
            cron_native_store.mark_run(job_id)
            logger.info("cron_native_job_done", job_id=job_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "cron_native_job_failed",
                job_id=job_id,
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
            )


# Синглтон
cron_native_scheduler = CronNativeScheduler()
