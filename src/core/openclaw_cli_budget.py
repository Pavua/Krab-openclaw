"""Глобальный budget для concurrent openclaw CLI subprocess'ов.

Wave 4: module-level Semaphore + reliable kill pattern.
Env: OPENCLAW_CLI_SPAWN_BUDGET (default 3).

Два семафора:
- get_global_semaphore()      — asyncio.Semaphore для async методов
- get_sync_semaphore()        — threading.Semaphore для sync/classmethod контекста
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

logger = logging.getLogger(__name__)

_BUDGET = int(os.getenv("OPENCLAW_CLI_SPAWN_BUDGET", "3"))
_GLOBAL_SEM: asyncio.Semaphore | None = None
_SYNC_SEM: threading.Semaphore | None = None


def get_global_semaphore() -> asyncio.Semaphore:
    """Lazy-init глобального семафора. Безопасно вызывать из разных async-контекстов."""
    global _GLOBAL_SEM  # noqa: PLW0603
    if _GLOBAL_SEM is None:
        _GLOBAL_SEM = asyncio.Semaphore(_BUDGET)
        logger.info("openclaw_cli_global_semaphore_init budget=%d", _BUDGET)
    return _GLOBAL_SEM


def get_sync_semaphore() -> threading.Semaphore:
    """Lazy-init sync семафора для subprocess.Popen в sync classmethod."""
    global _SYNC_SEM  # noqa: PLW0603
    if _SYNC_SEM is None:
        _SYNC_SEM = threading.Semaphore(_BUDGET)
        logger.info("openclaw_cli_sync_semaphore_init budget=%d", _BUDGET)
    return _SYNC_SEM


async def terminate_and_reap(
    proc: asyncio.subprocess.Process,
    term_grace: float = 2.0,
    kill_grace: float = 1.0,
) -> None:
    """SIGTERM → wait → SIGKILL → wait → warning если всё ещё не reap'ился.

    Предотвращает накопление zombie/orphan процессов после timeout'а.
    """
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=term_grace)
        return
    except asyncio.TimeoutError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=kill_grace)
    except asyncio.TimeoutError:
        logger.warning(
            "openclaw_cli_force_killed_but_no_reap pid=%s",
            getattr(proc, "pid", "?"),
        )
