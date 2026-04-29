"""Агрегатор данных для Dashboard V4: /api/dashboard/summary.

Собирает состояние всей экосистемы Краба в один JSON-ответ, чтобы
фронтенд Dashboard V4 мог обновлять главный view одним запросом, а не
пятнадцатью. Все источники данных опрашиваются с graceful fallback —
если любой из них недоступен, соответствующее поле становится None,
а endpoint не возвращает 500.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

_log = structlog.get_logger(__name__)

# Карта сервис → launchd label. Используется для проверки статуса через launchctl.
_SERVICE_LABELS: dict[str, str] = {
    "openclaw_gateway": "ai.openclaw.gateway",
    "mcp_yung_nagato": "com.krab.mcp-yung-nagato",
    "mcp_p0lrd": "com.krab.mcp-p0lrd",
    "mcp_hammerspoon": "com.krab.mcp-hammerspoon",
    "inbox_watcher": "ai.krab.inbox-watcher",
}


def _service_status_via_launchctl(label: str, *, timeout: float = 1.5) -> str:
    """Проверяет статус launchd-сервиса через `launchctl list <label>`.

    Возвращает "running" если процесс жив (PID > 0), иначе "down".
    На любой ошибке — "unknown", без выброса исключения.
    """

    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"

    if result.returncode != 0:
        return "down"

    # launchctl list <label> печатает plist-подобный вывод; ищем PID = <number>;
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('"PID"'):
            # формат: "PID" = 12345;
            try:
                value = stripped.split("=", 1)[1].strip().rstrip(";").strip()
                pid = int(value)
                return "running" if pid > 0 else "down"
            except (ValueError, IndexError):
                return "unknown"
    # Запись есть, но PID отсутствует → сервис загружен, но не запущен.
    return "down"


def _check_krab_process() -> tuple[str, int | None]:
    """Возвращает (status, pid) для userbot-процесса Краба.

    Пытается найти процесс по имени через pgrep. Если pgrep недоступен —
    ("unknown", None).
    """

    try:
        result = subprocess.run(
            ["pgrep", "-f", "userbot_bridge"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ("unknown", None)

    if result.returncode != 0 or not result.stdout.strip():
        return ("down", None)

    try:
        pid = int(result.stdout.strip().splitlines()[0])
        return ("running", pid)
    except (ValueError, IndexError):
        return ("unknown", None)


def _check_lm_studio() -> str:
    """Проверяет доступность LM Studio API (http://127.0.0.1:1234).

    Использует короткий TCP-probe: если сокет открывается — "running".
    Полный HTTP health-check избыточен для агрегатора.
    """

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", 1234))
        return "running"
    except (OSError, socket.timeout):
        return "down"
    finally:
        sock.close()


def collect_services_status(
    *,
    launchctl_check: Callable[[str], str] | None = None,
    krab_probe: Callable[[], tuple[str, int | None]] | None = None,
    lm_studio_probe: Callable[[], str] | None = None,
) -> tuple[dict[str, str], int | None]:
    """Собирает статусы всех ключевых сервисов.

    Возвращает (services_dict, krab_pid). Все зонды можно подменять в тестах.
    """

    launchctl_fn = launchctl_check or _service_status_via_launchctl
    krab_fn = krab_probe or _check_krab_process
    lm_fn = lm_studio_probe or _check_lm_studio

    services: dict[str, str] = {}
    krab_status, krab_pid = krab_fn()
    services["krab"] = krab_status

    for name, label in _SERVICE_LABELS.items():
        try:
            services[name] = launchctl_fn(label)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "dashboard_summary_service_probe_failed",
                service=name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            services[name] = "unknown"

    try:
        services["lm_studio"] = lm_fn()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_lm_studio_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        services["lm_studio"] = "unknown"

    return services, krab_pid


def collect_archive_block() -> dict[str, Any] | None:
    """Собирает компактный блок archive.db stats или None при ошибке."""

    try:
        from .memory_stats import collect_memory_stats

        stats = collect_memory_stats()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_archive_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    if not stats.get("exists"):
        return None

    return {
        "size_mb": stats.get("db_size_mb", 0.0),
        "message_count": stats.get("total_messages", 0),
        "encoded_chunks": stats.get("encoded_chunks", 0),
    }


def collect_memory_layer_block() -> dict[str, Any] | None:
    """Блок Memory Layer: total/encoded chunks + coverage."""

    try:
        from .memory_stats import collect_memory_stats

        stats = collect_memory_stats()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_memory_layer_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    if not stats.get("exists"):
        return None

    return {
        "total_chunks": stats.get("total_chunks", 0),
        "encoded_chunks": stats.get("encoded_chunks", 0),
        "coverage_pct": stats.get("encoding_coverage_pct", 0.0),
    }


def collect_activity_block() -> dict[str, Any] | None:
    """Компактный блок активности: команды сегодня, LLM-вызовы, ошибки.

    На текущий момент точного посуточного счётчика нет — используем
    суммарные счётчики command_registry как прокси "commands_total",
    а llm_calls/errors оставляем None до появления per-day source.
    """

    try:
        from .command_registry import get_usage

        usage = get_usage()
        commands_total = int(sum(usage.values())) if usage else 0
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_activity_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    return {
        "commands_today": commands_total,
        "llm_calls_today": None,
        "errors_today": None,
    }


def collect_alerts_block(router: Any) -> list[dict[str, Any]]:
    """Извлекает операционные алерты из router.get_ops_alerts()."""

    if router is None or not hasattr(router, "get_ops_alerts"):
        return []
    try:
        alerts = router.get_ops_alerts()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_alerts_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    if not isinstance(alerts, list):
        return []
    # Нормализуем поля: severity/code/msg. Прочие поля сохраняем как есть.
    normalized: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        normalized.append(
            {
                "severity": alert.get("severity", "info"),
                "code": alert.get("code", ""),
                "msg": alert.get("msg") or alert.get("message", ""),
                **{
                    k: v
                    for k, v in alert.items()
                    if k not in {"severity", "code", "msg", "message"}
                },
            }
        )
    return normalized


async def _run_subprocess_capture(*args: str, timeout: float = 1.5) -> tuple[int, str, str] | None:
    """Async wrapper над create_subprocess_exec + communicate с timeout.

    Возвращает (returncode, stdout, stderr) или None при ошибке/таймауте.
    Не блокирует event loop. Аргументы передаются напрямую (без shell).
    """

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception:  # noqa: BLE001
        return None

    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def _service_status_via_launchctl_async(label: str, *, timeout: float = 1.5) -> str:
    """Async версия _service_status_via_launchctl — не блокирует event loop."""

    result = await _run_subprocess_capture("launchctl", "list", label, timeout=timeout)
    if result is None:
        return "unknown"
    returncode, stdout, _ = result
    if returncode != 0:
        return "down"
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('"PID"'):
            try:
                value = stripped.split("=", 1)[1].strip().rstrip(";").strip()
                pid = int(value)
                return "running" if pid > 0 else "down"
            except (ValueError, IndexError):
                return "unknown"
    return "down"


async def _check_krab_process_async() -> tuple[str, int | None]:
    """Async версия _check_krab_process."""

    result = await _run_subprocess_capture("pgrep", "-f", "userbot_bridge", timeout=1.5)
    if result is None:
        return ("unknown", None)
    returncode, stdout, _ = result
    if returncode != 0 or not stdout.strip():
        return ("down", None)
    try:
        pid = int(stdout.strip().splitlines()[0])
        return ("running", pid)
    except (ValueError, IndexError):
        return ("unknown", None)


async def _check_lm_studio_async() -> str:
    """Async TCP-probe LM Studio через open_connection с таймаутом."""

    try:
        fut = asyncio.open_connection("127.0.0.1", 1234)
        _reader, writer = await asyncio.wait_for(fut, timeout=0.5)
    except (OSError, asyncio.TimeoutError):
        return "down"
    try:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return "running"


async def collect_services_status_async() -> tuple[dict[str, str], int | None]:
    """Async-версия collect_services_status: все пробы летят параллельно.

    Запускаем 6 subprocess'ов (1 pgrep + 5 launchctl) + 1 TCP-probe одновременно
    через asyncio.gather. Общее время ≈ max(probe), а не sum(probes).
    """

    krab_task = asyncio.create_task(_check_krab_process_async())
    launchctl_tasks = {
        name: asyncio.create_task(_service_status_via_launchctl_async(label))
        for name, label in _SERVICE_LABELS.items()
    }
    lm_task = asyncio.create_task(_check_lm_studio_async())

    services: dict[str, str] = {}

    try:
        krab_status, krab_pid = await krab_task
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_krab_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        krab_status, krab_pid = "unknown", None
    services["krab"] = krab_status

    for name, task in launchctl_tasks.items():
        try:
            services[name] = await task
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "dashboard_summary_service_probe_failed",
                service=name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            services[name] = "unknown"

    try:
        services["lm_studio"] = await lm_task
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_lm_studio_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        services["lm_studio"] = "unknown"

    return services, krab_pid


async def collect_dashboard_summary_async(
    *,
    boot_ts: float | None = None,
    router: Any = None,
    services_probe: Callable[[], Awaitable[tuple[dict[str, str], int | None]]] | None = None,
    archive_probe: Callable[[], dict[str, Any] | None] | None = None,
    memory_probe: Callable[[], dict[str, Any] | None] | None = None,
    activity_probe: Callable[[], dict[str, Any] | None] | None = None,
    alerts_probe: Callable[[Any], list[dict[str, Any]]] | None = None,
    version_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Async-агрегатор: не блокирует event loop, параллелит subprocess-пробы.

    Лёгкие чтения (archive/memory/activity/alerts) — inline (быстрые in-memory/SQLite).
    Тяжёлые subprocess-пробы (launchctl/pgrep/socket) идут через asyncio.gather
    внутри collect_services_status_async.
    """

    started = time.perf_counter()
    sources_queried: list[str] = []

    now = time.time()
    boot = boot_ts if boot_ts is not None else now
    uptime = {"sec": int(round(now - boot)), "boot_ts": boot}
    sources_queried.append("uptime")

    version = version_info or {"session": "13", "krab_version": "v8"}
    sources_queried.append("version")

    services_fn = services_probe or collect_services_status_async
    try:
        services, krab_pid = await services_fn()
        sources_queried.append("services")
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_services_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        services, krab_pid = {}, None

    archive_fn = archive_probe or collect_archive_block
    archive = archive_fn()
    if archive is not None:
        sources_queried.append("archive")

    memory_fn = memory_probe or collect_memory_layer_block
    memory_layer = memory_fn()
    if memory_layer is not None:
        sources_queried.append("memory_layer")

    activity_fn = activity_probe or collect_activity_block
    activity = activity_fn()
    if activity is not None:
        sources_queried.append("activity")

    alerts_fn = alerts_probe or collect_alerts_block
    alerts = alerts_fn(router)
    sources_queried.append("alerts")

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "ok": True,
        "uptime": uptime,
        "version": version,
        "krab_pid": krab_pid,
        "services": services,
        "archive": archive,
        "memory_layer": memory_layer,
        "activity": activity,
        "alerts": alerts,
        "_meta": {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources_queried": sources_queried,
            "elapsed_ms": elapsed_ms,
        },
    }


def collect_dashboard_summary(
    *,
    boot_ts: float | None = None,
    router: Any = None,
    services_probe: (Callable[[], tuple[dict[str, str], int | None]] | None) = None,
    archive_probe: Callable[[], dict[str, Any] | None] | None = None,
    memory_probe: Callable[[], dict[str, Any] | None] | None = None,
    activity_probe: Callable[[], dict[str, Any] | None] | None = None,
    alerts_probe: Callable[[Any], list[dict[str, Any]]] | None = None,
    version_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Главный агрегатор.

    Все источники инъектируются для тестируемости: подменяем пробы
    на заглушки в юнит-тестах. В production вызывается с дефолтами.
    """

    started = time.perf_counter()
    sources_queried: list[str] = []

    # Uptime
    now = time.time()
    boot = boot_ts if boot_ts is not None else now
    uptime = {"sec": int(round(now - boot)), "boot_ts": boot}
    sources_queried.append("uptime")

    # Version
    version = version_info or {"session": "13", "krab_version": "v8"}
    sources_queried.append("version")

    # Services + krab pid
    services_fn = services_probe or collect_services_status
    try:
        services, krab_pid = services_fn()
        sources_queried.append("services")
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "dashboard_summary_services_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        services, krab_pid = {}, None

    # Archive
    archive_fn = archive_probe or collect_archive_block
    archive = archive_fn()
    if archive is not None:
        sources_queried.append("archive")

    # Memory layer
    memory_fn = memory_probe or collect_memory_layer_block
    memory_layer = memory_fn()
    if memory_layer is not None:
        sources_queried.append("memory_layer")

    # Activity
    activity_fn = activity_probe or collect_activity_block
    activity = activity_fn()
    if activity is not None:
        sources_queried.append("activity")

    # Alerts
    alerts_fn = alerts_probe or collect_alerts_block
    alerts = alerts_fn(router)
    sources_queried.append("alerts")

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    return {
        "ok": True,
        "uptime": uptime,
        "version": version,
        "krab_pid": krab_pid,
        "services": services,
        "archive": archive,
        "memory_layer": memory_layer,
        "activity": activity,
        "alerts": alerts,
        "_meta": {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources_queried": sources_queried,
            "elapsed_ms": elapsed_ms,
        },
    }
