# -*- coding: utf-8 -*-
"""
Wave 79: health probe для Krab Ear backend (по умолчанию http://127.0.0.1:5005/health).

Дополняет on-scrape проверку из ecosystem_health.py отдельным фоновым loop'ом,
который копит статистику отказов и экспонирует её в Prometheus. Цель — ловить
регрессии тип Session 40 (SingleInstanceGuard deadlock → backend жив, но KE UI
зависал) и алертить когда KE backend перестал отвечать N итераций подряд.

Метрики (экспонируются через src.core.prometheus_metrics.collect_metrics):
    krab_ear_probe_last_ago_seconds       — секунд с последнего успешного probe
    krab_ear_probe_failures_total{reason} — отказы по причинам (timeout/5xx/connection_error)
    krab_ear_consecutive_failures         — текущая длина streak отказов

Pattern: повторяет launchd_health_monitor (Wave 75) — module-level snapshot,
asyncio background task, env-gate в bootstrap.
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from typing import Any, Callable

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Дефолтный backend URL — синхронизирован с ecosystem_health.py.
_DEFAULT_BACKEND_URL = "http://127.0.0.1:5005"
_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_TIMEOUT_SEC = 5.0

# Snapshot: module-level, читается on-scrape из prometheus_metrics.collect_metrics().
_SNAPSHOT: dict[str, Any] = {
    "last_probe_ts": 0.0,
    "last_success_ts": 0.0,
    "last_probe_ok": False,
    "consecutive_failures": 0,
    "total_failures": 0,
    "failures_by_reason": {},  # reason → count
}


def get_snapshot() -> dict[str, Any]:
    """Копия текущего snapshot — для prometheus_metrics.collect_metrics()."""
    snap = dict(_SNAPSHOT)
    snap["failures_by_reason"] = dict(_SNAPSHOT["failures_by_reason"])
    return snap


def _classify_failure(exc: BaseException | None, status_code: int | None) -> str:
    """Маппит исключение/HTTP статус в reason label."""
    if exc is not None:
        if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "connection_error"
        if isinstance(exc, httpx.HTTPError):
            return "http_error"
        return "exception"
    if status_code is not None:
        if 500 <= status_code < 600:
            return "5xx"
        if 400 <= status_code < 500:
            return "4xx"
    return "unknown"


def _record_success(*, now: float) -> None:
    _SNAPSHOT["last_probe_ts"] = now
    _SNAPSHOT["last_success_ts"] = now
    _SNAPSHOT["last_probe_ok"] = True
    _SNAPSHOT["consecutive_failures"] = 0


def _record_failure(*, reason: str, now: float) -> None:
    _SNAPSHOT["last_probe_ts"] = now
    _SNAPSHOT["last_probe_ok"] = False
    _SNAPSHOT["consecutive_failures"] = int(_SNAPSHOT.get("consecutive_failures", 0)) + 1
    _SNAPSHOT["total_failures"] = int(_SNAPSHOT.get("total_failures", 0)) + 1
    bucket: dict[str, int] = _SNAPSHOT.setdefault("failures_by_reason", {})
    bucket[reason] = bucket.get(reason, 0) + 1


class KrabEarHealthProbe:
    """Фоновый probe Krab Ear backend /health."""

    def __init__(
        self,
        *,
        backend_url: str | None = None,
        interval_sec: int | None = None,
        timeout_sec: float | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._backend_url = (
            backend_url or os.getenv("KRAB_EAR_BACKEND_URL", _DEFAULT_BACKEND_URL)
        ).rstrip("/")
        self._interval = max(
            5, int(interval_sec or os.getenv("KRAB_EAR_PROBE_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC))
        )
        self._timeout = float(timeout_sec or _DEFAULT_TIMEOUT_SEC)
        self._http_client_factory = http_client_factory
        self._now_fn = now_fn or time.time
        self._task: asyncio.Task | None = None

    @property
    def health_url(self) -> str:
        return f"{self._backend_url}/health"

    def _make_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=self._timeout)

    async def probe_once(self) -> bool:
        """Одна итерация probe. Обновляет module-level snapshot. True = успех."""
        now = self._now_fn()
        try:
            async with self._make_client() as client:
                response = await client.get(self.health_url)
                status = response.status_code
            if status == 200:
                _record_success(now=now)
                return True
            reason = _classify_failure(None, status)
            _record_failure(reason=reason, now=now)
            logger.warning(
                "krab_ear_probe_bad_status",
                url=self.health_url,
                status=status,
                consecutive_failures=_SNAPSHOT["consecutive_failures"],
            )
            return False
        except Exception as exc:  # noqa: BLE001
            reason = _classify_failure(exc, None)
            _record_failure(reason=reason, now=now)
            logger.warning(
                "krab_ear_probe_failed",
                url=self.health_url,
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
                consecutive_failures=_SNAPSHOT["consecutive_failures"],
            )
            return False

    def start(self) -> None:
        """Запускает background loop (идемпотентен)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="krab_ear_health_probe")
        logger.info(
            "krab_ear_health_probe_started",
            url=self.health_url,
            interval_sec=self._interval,
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.probe_once()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                logger.info("krab_ear_health_probe_stopped")
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "krab_ear_health_probe_loop_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
                # Не выходим, чтобы регрессия в probe_once не убила фоновый loop.
                await asyncio.sleep(self._interval)


# Module-level singleton — bootstrap из userbot_bridge.start().
krab_ear_health_probe = KrabEarHealthProbe()


def reset_snapshot_for_tests() -> None:
    """Только для тестов: обнуляет module-level snapshot."""
    _SNAPSHOT["last_probe_ts"] = 0.0
    _SNAPSHOT["last_success_ts"] = 0.0
    _SNAPSHOT["last_probe_ok"] = False
    _SNAPSHOT["consecutive_failures"] = 0
    _SNAPSHOT["total_failures"] = 0
    _SNAPSHOT["failures_by_reason"] = {}
