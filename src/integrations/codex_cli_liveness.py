"""Wave 19-D: psutil subprocess liveness check для codex-cli.

Wave 16-I (idle-aware) отслеживает stream chunks. НО если codex-cli subprocess
deadlock'нулся на C-level / sigblock — нет chunks, но и нет crash. Wave 19-D
проверяет реально ли process active через psutil:

- find codex subprocess (parent = openclaw gateway или Krab self)
- check status (RUNNING, SLEEPING acceptable; ZOMBIE, STOPPED → dead)
- check CPU activity (>0.5% за последний 30s = живой)
- если frozen — explicit kill + report через LLMRetryableError
"""

from __future__ import annotations

import time
from typing import Any

import psutil

from ..core.logger import get_logger

logger = get_logger(__name__)

# Ключевые слова для поиска codex-cli процесса по имени/cmdline
CODEX_PROCESS_HINTS = ("codex", "codex-cli")

# Статусы, при которых процесс считается «живым» (не завис навсегда)
_ALIVE_STATUSES = (
    psutil.STATUS_RUNNING,
    psutil.STATUS_SLEEPING,
    psutil.STATUS_DISK_SLEEP,
)


def find_codex_processes() -> list[psutil.Process]:
    """Возвращает список psutil.Process, соответствующих codex-cli signature.

    Поиск по имени процесса и полной cmdline строке.
    Ошибки доступа и «пропавшие» процессы — тихо игнорируются.
    """
    matches: list[psutil.Process] = []
    try:
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                name = (proc.info.get("name") or "").lower()
                if any(hint in cmdline.lower() or hint in name for hint in CODEX_PROCESS_HINTS):
                    matches.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Процесс мог завершиться пока мы итерировали
                continue
    except Exception as exc:  # noqa: BLE001
        # psutil.process_iter сам может выбросить на некоторых платформах
        logger.warning("codex_liveness_iter_error", error=str(exc))
    return matches


def is_codex_alive(
    min_cpu_percent: float = 0.5,
    sample_window_sec: float = 5.0,
) -> dict[str, Any]:
    """Проверяет, жив ли хоть один codex-cli процесс.

    Процесс считается «активным» если:
    - его status в списке ALIVE_STATUSES (не ZOMBIE / STOPPED)
    - и CPU usage >= min_cpu_percent за sample_window_sec

    Args:
        min_cpu_percent: минимальный CPU% для признания процесса активным.
        sample_window_sec: интервал замера CPU (передаётся в psutil cpu_percent).

    Returns:
        {
            "alive": bool,           — True если хотя бы один процесс активен
            "process_count": int,    — всего найдено codex процессов
            "active_count": int,     — процессов с CPU > min_cpu_percent
            "details": list[dict],   — подробности по каждому процессу
        }
    """
    procs = find_codex_processes()
    details: list[dict[str, Any]] = []
    active_count = 0

    for proc in procs:
        try:
            # Первый вызов cpu_percent без интервала сбрасывает счётчик
            proc.cpu_percent(interval=None)
            # Короткий sleep перед замером — иначе дельта == 0
            time.sleep(0.1)
            cpu = proc.cpu_percent(interval=sample_window_sec)

            status = proc.status()
            mem_rss = proc.memory_info().rss / 1024 / 1024  # в МиБ
            age_sec = time.time() - proc.create_time()

            is_active_status = status in _ALIVE_STATUSES
            is_active_cpu = cpu >= min_cpu_percent

            if is_active_status and is_active_cpu:
                active_count += 1

            details.append(
                {
                    "pid": proc.pid,
                    "name": proc.info.get("name") or "",
                    "status": status,
                    "cpu_percent": round(cpu, 2),
                    "rss_mb": round(mem_rss, 1),
                    "age_sec": round(age_sec, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning(
                "codex_liveness_proc_error",
                pid=proc.pid,
                error=str(exc),
            )

    return {
        "alive": active_count > 0,
        "process_count": len(procs),
        "active_count": active_count,
        "details": details,
    }


def kill_codex_processes(*, force: bool = False) -> dict[str, Any]:
    """Завершает все codex-cli процессы.

    По умолчанию — мягкое завершение (SIGTERM).
    Если force=True — SIGKILL (немедленное убийство).

    Args:
        force: если True — kill() вместо terminate().

    Returns:
        {
            "killed": list[int],   — pid'ы успешно завершённых процессов
            "errors": list[str],   — ошибки при завершении
        }
    """
    result: dict[str, Any] = {"killed": [], "errors": []}
    for proc in find_codex_processes():
        try:
            if force:
                proc.kill()  # SIGKILL
            else:
                proc.terminate()  # SIGTERM
            result["killed"].append(proc.pid)
            logger.info(
                "codex_process_killed",
                pid=proc.pid,
                force=force,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            result["errors"].append(f"pid={proc.pid}: {exc}")
            logger.warning(
                "codex_kill_error",
                pid=proc.pid,
                error=str(exc),
            )
    return result
