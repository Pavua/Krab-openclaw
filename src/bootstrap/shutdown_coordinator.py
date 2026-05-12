"""Wave 101: централизованный shutdown coordinator.

Регистрирует drain-функции background-компонентов (cron, monitors, queues,
caches) и orderly-сливает их при SIGTERM/SIGINT.

Drains выполняются в **LIFO** порядке: компоненты, зарегистрированные позже
(обычно более высокоуровневые), останавливаются первыми. Каждый drain имеет
свой timeout (default 5s); медленные drain'ы логируются, исключения внутри
drain не прерывают цепочку.

Использование:

    from src.bootstrap.shutdown_coordinator import shutdown_coordinator

    shutdown_coordinator.register(
        "swarm_activity_log",
        swarm_activity_log.flush,
        timeout_sec=3,
    )

    # На SIGTERM:
    await shutdown_coordinator.drain_all()
"""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)

# Prometheus Histogram — silent no-op если prometheus_client недоступен.
try:
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_shutdown_drain_duration_seconds = _Histogram(
        "krab_shutdown_drain_duration_seconds",
        "Длительность drain каждого зарегистрированного компонента (Wave 101)",
        ["component"],
        buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_shutdown_drain_duration_seconds = None  # type: ignore[assignment]


DrainFn = Callable[[], Awaitable[None]]
"""Async drain-функция: должна корректно flush'ить состояние и вернуться."""

# Дефолтный timeout per drain — баланс между быстротой shutdown и safety.
DEFAULT_DRAIN_TIMEOUT_SEC: float = 5.0
# Порог "медленного" drain — выше → warning в логах.
SLOW_DRAIN_THRESHOLD_SEC: float = 1.0


@dataclass(slots=True)
class _Entry:
    """Registry-запись одного зарегистрированного drain'а."""

    name: str
    drain_fn: DrainFn
    timeout_sec: float


@dataclass(slots=True)
class DrainOutcome:
    """Результат drain одного компонента."""

    name: str
    duration_sec: float
    status: str  # "ok" | "timeout" | "error"
    error: str | None = None


@dataclass(slots=True)
class ShutdownCoordinator:
    """Координатор graceful shutdown.

    Drain'ы выполняются в LIFO порядке (reversed registry).
    Не singleton по дизайну — модульная переменная `shutdown_coordinator`
    в конце файла играет роль глобальной точки доступа.
    """

    _registry: list[_Entry] = field(default_factory=list)
    _draining: bool = False
    _signal_handlers_installed: bool = False

    def register(
        self,
        name: str,
        drain_fn: DrainFn,
        timeout_sec: float = DEFAULT_DRAIN_TIMEOUT_SEC,
    ) -> None:
        """Регистрирует компонент в registry.

        Дубликаты по имени не подавляются — последняя регистрация выполняется
        первой (LIFO), но и предыдущая тоже отработает. Это намеренно: явные
        предупреждения вместо тихого silent-override.
        """
        if not name:
            raise ValueError("shutdown_coordinator_register_requires_name")
        if not callable(drain_fn):
            raise TypeError("drain_fn_must_be_callable")
        if timeout_sec <= 0:
            raise ValueError("timeout_sec_must_be_positive")

        if any(e.name == name for e in self._registry):
            logger.warning(
                "shutdown_coordinator_duplicate_name",
                component=name,
                hint="both_drains_will_run",
            )

        self._registry.append(_Entry(name=name, drain_fn=drain_fn, timeout_sec=timeout_sec))
        logger.debug(
            "shutdown_coordinator_registered",
            component=name,
            timeout_sec=timeout_sec,
            total=len(self._registry),
        )

    def unregister(self, name: str) -> int:
        """Удаляет все записи с указанным name. Возвращает кол-во удалённых."""
        before = len(self._registry)
        self._registry = [e for e in self._registry if e.name != name]
        return before - len(self._registry)

    def clear(self) -> None:
        """Сбрасывает registry. В основном для тестов."""
        self._registry.clear()
        self._draining = False

    def registered_names(self) -> list[str]:
        """Список имён в порядке регистрации (FIFO). Возвращает копию."""
        return [e.name for e in self._registry]

    async def drain_all(self) -> list[DrainOutcome]:
        """Сливает все компоненты в LIFO порядке.

        Не raise'ит — собирает outcomes и возвращает. Повторный вызов
        идемпотентен: второй раз вернёт пустой список (registry уже пуст).
        """
        if self._draining:
            logger.warning("shutdown_coordinator_drain_already_in_progress")
            return []
        self._draining = True
        outcomes: list[DrainOutcome] = []
        try:
            # LIFO: reversed registry.
            entries = list(reversed(self._registry))
            logger.info(
                "shutdown_coordinator_drain_start",
                count=len(entries),
                order=[e.name for e in entries],
            )
            for entry in entries:
                outcome = await self._drain_one(entry)
                outcomes.append(outcome)
            # Registry зачищаем после успешного цикла — повторный drain_all
            # не сделает дубль flush.
            self._registry.clear()
            logger.info(
                "shutdown_coordinator_drain_done",
                total=len(outcomes),
                ok=sum(1 for o in outcomes if o.status == "ok"),
                timeout=sum(1 for o in outcomes if o.status == "timeout"),
                error=sum(1 for o in outcomes if o.status == "error"),
            )
        finally:
            self._draining = False
        return outcomes

    async def _drain_one(self, entry: _Entry) -> DrainOutcome:
        """Сливает один компонент с timeout и exception isolation."""
        start = time.monotonic()
        status = "ok"
        err: str | None = None
        try:
            await asyncio.wait_for(entry.drain_fn(), timeout=entry.timeout_sec)
        except asyncio.TimeoutError:
            status = "timeout"
            err = f"timeout_after_{entry.timeout_sec}s"
            logger.warning(
                "shutdown_drain_timeout",
                component=entry.name,
                timeout_sec=entry.timeout_sec,
            )
        except asyncio.CancelledError:
            # Cancellation должен пропускаться выше — не глотаем.
            raise
        except Exception as exc:  # noqa: BLE001
            status = "error"
            err = str(exc)
            logger.warning(
                "shutdown_drain_error",
                component=entry.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        duration = time.monotonic() - start

        if krab_shutdown_drain_duration_seconds is not None:
            try:
                krab_shutdown_drain_duration_seconds.labels(component=entry.name).observe(duration)
            except Exception as metric_exc:  # noqa: BLE001
                logger.debug(
                    "shutdown_drain_metric_failed",
                    component=entry.name,
                    error=str(metric_exc),
                )

        if duration >= SLOW_DRAIN_THRESHOLD_SEC and status == "ok":
            logger.warning(
                "shutdown_drain_slow",
                component=entry.name,
                duration_sec=round(duration, 3),
                threshold_sec=SLOW_DRAIN_THRESHOLD_SEC,
            )

        return DrainOutcome(
            name=entry.name,
            duration_sec=duration,
            status=status,
            error=err,
        )

    def install_signal_handlers(
        self,
        loop: asyncio.AbstractEventLoop,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Регистрирует SIGTERM/SIGINT handlers на event loop.

        При получении сигнала: drain_all() → optional on_done callback.
        Идемпотентен — повторный вызов silent-skips.
        """
        if self._signal_handlers_installed:
            logger.debug("shutdown_coordinator_signal_handlers_already_installed")
            return

        def _handle(signame: str) -> None:
            logger.info("shutdown_signal_received", signal=signame)
            asyncio.ensure_future(self._signal_drain(on_done), loop=loop)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle, sig.name)
            except (NotImplementedError, RuntimeError) as exc:
                # Windows / некоторые embedded loops — fallback silent.
                logger.debug(
                    "shutdown_coordinator_signal_install_skipped",
                    signal=sig.name,
                    error=str(exc),
                )

        self._signal_handlers_installed = True

    async def _signal_drain(self, on_done: Callable[[], None] | None) -> None:
        try:
            await self.drain_all()
        finally:
            if on_done is not None:
                try:
                    on_done()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "shutdown_coordinator_on_done_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )


# Module-level singleton — точка доступа из bootstrap/runtime.
shutdown_coordinator = ShutdownCoordinator()


def register_default_drains() -> None:
    """Регистрирует стандартные компоненты Krab (Wave 101 baseline).

    Вызывается из bootstrap после того как все компоненты импортированы.
    Импорты внутри функции, чтобы избежать circular imports при загрузке
    модуля bootstrap.
    """
    # Wave 89: swarm activity log flush.
    try:
        from src.core.swarm_activity_log import swarm_activity_log  # type: ignore[import-not-found]

        if hasattr(swarm_activity_log, "flush"):
            shutdown_coordinator.register(
                "swarm_activity_log",
                _wrap_maybe_sync(swarm_activity_log.flush),
                timeout_sec=3.0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("register_default_drains_swarm_activity_log_skip", error=str(exc))

    # Wave 95: translation cache persist.
    try:
        from src.core.translation_cache import translation_cache  # type: ignore[import-not-found]

        if hasattr(translation_cache, "persist"):
            shutdown_coordinator.register(
                "translation_cache",
                _wrap_maybe_sync(translation_cache.persist),
                timeout_sec=3.0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("register_default_drains_translation_cache_skip", error=str(exc))

    # Wave 79: krab ear health probe.
    try:
        from src.core.krab_ear_health_probe import (  # type: ignore[import-not-found]
            krab_ear_health_probe,
        )

        if hasattr(krab_ear_health_probe, "stop"):
            shutdown_coordinator.register(
                "krab_ear_health_probe",
                _wrap_maybe_sync(krab_ear_health_probe.stop),
                timeout_sec=2.0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("register_default_drains_krab_ear_health_probe_skip", error=str(exc))

    # Wave 75: launchd health monitor.
    try:
        from src.core.launchd_health_monitor import (  # type: ignore[import-not-found]
            launchd_health_monitor,
        )

        if hasattr(launchd_health_monitor, "stop"):
            shutdown_coordinator.register(
                "launchd_health_monitor",
                _wrap_maybe_sync(launchd_health_monitor.stop),
                timeout_sec=2.0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("register_default_drains_launchd_health_monitor_skip", error=str(exc))


def _wrap_maybe_sync(fn: Callable[[], object]) -> DrainFn:
    """Превращает sync- или async-функцию в DrainFn (async no-arg)."""

    async def _wrapped() -> None:
        result = fn()
        if asyncio.iscoroutine(result):
            await result

    return _wrapped


__all__ = [
    "ShutdownCoordinator",
    "DrainOutcome",
    "shutdown_coordinator",
    "register_default_drains",
    "krab_shutdown_drain_duration_seconds",
    "DEFAULT_DRAIN_TIMEOUT_SEC",
    "SLOW_DRAIN_THRESHOLD_SEC",
]
