"""Семафорный бюджет для CLI-вызовов openclaw.

Проблема: без контроля конкурентности короткоживущие CLI-вызовы
(openclaw models status, openclaw cron list, openclaw channels status …)
накапливаются, если endpoint или proactive_watch тригерятся одновременно
— наблюдался лик до 20 процессов.

Решение: единый asyncio.Semaphore(budget=3) для всех transient CLI-вызовов
+ утилита terminate_and_reap() для принудительного завершения зависших
процессов с ожиданием.

Steady-state ожидаемые openclaw-процессы (НЕ через этот семафор):
  1  openclaw-gateway  (LaunchAgent ai.openclaw.gateway)
  1  Chrome --remote-debugging-port=18800  (dedicated browser)

Все transient CLI: openclaw models status/list, openclaw cron list,
openclaw channels status, openclaw secrets reload, openclaw logs — обязаны
проходить через acquire()/release().
"""

from __future__ import annotations

import asyncio
import os

import structlog

logger = structlog.get_logger(__name__)

# Максимальное число одновременных transient CLI-вызовов openclaw.
OPENCLAW_CLI_BUDGET: int = int(os.getenv("OPENCLAW_CLI_BUDGET", "3"))

_sem: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    """Ленивая инициализация семафора в event loop."""
    global _sem  # noqa: PLW0603
    if _sem is None:
        _sem = asyncio.Semaphore(OPENCLAW_CLI_BUDGET)
    return _sem


def reset_semaphore(budget: int = OPENCLAW_CLI_BUDGET) -> None:
    """Пересоздаёт семафор (только для тестов или после изменения BUDGET)."""
    global _sem  # noqa: PLW0603
    _sem = asyncio.Semaphore(budget)


class _BudgetContext:
    """Async context manager: acquire -> yield -> release."""

    async def __aenter__(self) -> "_BudgetContext":
        sem = _get_sem()
        await sem.acquire()
        logger.debug("openclaw_cli_budget_acquired", available=sem._value)
        return self

    async def __aexit__(self, *_: object) -> None:
        sem = _get_sem()
        sem.release()
        logger.debug("openclaw_cli_budget_released", available=sem._value)


def acquire() -> _BudgetContext:
    """Использование:

    async with openclaw_cli_budget.acquire():
        proc = await asyncio.create_subprocess_exec("openclaw", ...)
        await proc.communicate()
    """
    return _BudgetContext()


def budget_available() -> int:
    """Возвращает текущее число свободных слотов (0 -> все заняты)."""
    sem = _get_sem()
    return int(sem._value)


async def terminate_and_reap(
    proc: "asyncio.subprocess.Process",
    *,
    timeout_sec: float = 5.0,
) -> None:
    """Принудительно завершает subprocess и ждёт его смерти.

    Сначала SIGTERM, если не умер за timeout -> SIGKILL.
    Не поднимает исключений — безопасен в finally-блоках.
    """
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def list_openclaw_procs() -> list[dict[str, object]]:
    """Возвращает список текущих openclaw-процессов (через psutil).

    Каждый элемент: {pid, cmd, age_sec, rss_mb, status, is_gateway}.
    Если psutil недоступен — пустой список без исключения.
    """
    try:
        import time

        import psutil
    except ImportError:
        return []

    result: list[dict[str, object]] = []
    now = time.time()
    for proc in psutil.process_iter(
        ["pid", "name", "cmdline", "create_time", "status", "memory_info"]
    ):
        try:
            cmdline: list[str] = proc.info["cmdline"] or []
            name: str = proc.info["name"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        # Совпадение: имя процесса содержит "openclaw" или первый аргумент cmdline содержит "openclaw"
        is_openclaw = "openclaw" in name.lower() or (
            cmdline and "openclaw" in (cmdline[0] or "").lower()
        )
        # Отфильтровываем Chrome-процессы, у которых openclaw только в --user-data-dir
        is_chrome = bool(
            cmdline
            and any("Google Chrome" in arg or "Chrome Helper" in arg for arg in cmdline if arg)
        )
        if not is_openclaw or is_chrome:
            continue

        try:
            rss = (proc.info["memory_info"].rss / 1_048_576) if proc.info["memory_info"] else 0.0
        except Exception:  # noqa: BLE001
            rss = 0.0

        try:
            age_sec = now - float(proc.info["create_time"] or now)
        except Exception:  # noqa: BLE001
            age_sec = 0.0

        cmd_str = " ".join(str(a) for a in (cmdline[:8] if cmdline else [])) or name
        is_gateway = "openclaw-gateway" in name.lower() or (
            cmdline and "openclaw-gateway" in (cmdline[0] or "").lower()
        )
        result.append(
            {
                "pid": proc.info["pid"],
                "cmd": cmd_str,
                "age_sec": round(float(age_sec), 1),
                "rss_mb": round(float(rss), 1),
                "status": proc.info["status"] or "unknown",
                "is_gateway": is_gateway,
            }
        )
    return result
