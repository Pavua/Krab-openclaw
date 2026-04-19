# -*- coding: utf-8 -*-
"""
Сборщик расширенной диагностики Краба — базовая логика для /api/health/deep
и !health deep команды.

Возвращает структурированный dict; форматирование в markdown выполняется
на стороне вызывающего кода (command_handlers, web_app).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


async def collect_health_deep(
    *,
    session_start_time: float | None = None,
) -> dict[str, Any]:
    """Собирает 8 секций диагностики и возвращает структурированный dict.

    Args:
        session_start_time: время старта userbot сессии (time.time()).
                            None → uptime будет -1.

    Returns:
        dict с ключами: krab, openclaw, lm_studio, archive_db,
        reminders, memory_validator, sigterm_recent_count, system.
        Каждая секция содержит ``error`` str при сбое.
    """
    import psutil

    from ..config import config
    from ..core.lm_studio_health import (
        fetch_lm_studio_models_list,
        is_lm_studio_available,
    )
    from ..core.memory_validator import memory_validator as _mv
    from ..core.openclaw_runtime_models import get_runtime_primary_model
    from ..core.scheduler import krab_scheduler as _ks
    from ..core.subprocess_env import clean_subprocess_env
    from ..openclaw_client import openclaw_client

    result: dict[str, Any] = {}

    # ── 1. Krab process ─────────────────────────────────────────────────────
    try:
        uptime_sec = int(time.time() - session_start_time) if session_start_time is not None else -1
        proc = psutil.Process(os.getpid())
        rss_mb = int(proc.memory_info().rss / 1024 / 1024)
        load1, load5, _ = os.getloadavg()
        result["krab"] = {
            "uptime_sec": uptime_sec,
            "rss_mb": rss_mb,
            "cpu_pct": round(load1, 2),
        }
    except Exception as exc:  # noqa: BLE001
        result["krab"] = {"error": str(exc), "error_type": type(exc).__name__}

    # ── 2. OpenClaw gateway ──────────────────────────────────────────────────
    try:
        oc_ok = await openclaw_client.health_check()
        route_meta: dict[str, Any] = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            route_meta = openclaw_client.get_last_runtime_route() or {}
        if not route_meta.get("model"):
            route_meta["model"] = (
                str(get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown")
            )
        result["openclaw"] = {
            "healthy": bool(oc_ok),
            "last_route": route_meta,
        }
    except Exception as exc:  # noqa: BLE001
        result["openclaw"] = {
            "healthy": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    # ── 3. LM Studio ─────────────────────────────────────────────────────────
    try:
        lm_ok = await is_lm_studio_available(config.LM_STUDIO_URL, timeout=2.0)
        if lm_ok:
            lm_models = await fetch_lm_studio_models_list(config.LM_STUDIO_URL, timeout=3.0)
            active = [m.get("name") or m.get("id", "?") for m in (lm_models or [])[:3]]
            result["lm_studio"] = {
                "state": "online",
                "active_model": active[0] if active else None,
                "loaded_models": active,
            }
        else:
            result["lm_studio"] = {"state": "offline", "active_model": None}
    except Exception as exc:  # noqa: BLE001
        result["lm_studio"] = {
            "state": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    # ── 4. Archive.db integrity ──────────────────────────────────────────────
    try:
        db_path = Path("~/.openclaw/krab_memory/archive.db").expanduser()
        if db_path.exists():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                fts_orphans: int | None
                vec_orphans: int | None
                try:
                    # Orphan = строка в FTS shadow table без соответствующего chunks.id
                    fts_orphans = conn.execute(
                        """
                        SELECT COUNT(*) FROM messages_fts_docsize AS d
                        LEFT JOIN chunks AS c ON c.id = d.id
                        WHERE c.id IS NULL
                        """
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    fts_orphans = None
                try:
                    # Загружаем sqlite-vec extension для доступа к vec_chunks_rowids
                    import sqlite_vec  # type: ignore[import-not-found]

                    conn.enable_load_extension(True)
                    try:
                        sqlite_vec.load(conn)
                    finally:
                        conn.enable_load_extension(False)
                    vec_orphans = conn.execute(
                        """
                        SELECT COUNT(*) FROM vec_chunks_rowids AS vr
                        LEFT JOIN chunks AS c ON c.id = vr.id
                        WHERE c.id IS NULL
                        """
                    ).fetchone()[0]
                except Exception:  # noqa: BLE001
                    vec_orphans = None
            finally:
                conn.close()
            size_mb = round(db_path.stat().st_size / 1024 / 1024, 2)
            result["archive_db"] = {
                "integrity": integrity,
                "messages": msg_count,
                "chunks": chunk_count,
                "size_mb": size_mb,
                "orphan_fts5": fts_orphans,
                "orphan_vec": vec_orphans,
            }
        else:
            result["archive_db"] = {"integrity": "missing", "orphan_fts5": 0, "orphan_vec": 0}
    except Exception as exc:  # noqa: BLE001
        result["archive_db"] = {
            "integrity": "error",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    # ── 5. Reminders pending ─────────────────────────────────────────────────
    try:
        all_reminders = _ks.list_reminders()
        result["reminders"] = {"pending": len(all_reminders)}
    except Exception as exc:  # noqa: BLE001
        result["reminders"] = {"pending": -1, "error": str(exc), "error_type": type(exc).__name__}

    # ── 6. Memory validator pending confirms ─────────────────────────────────
    try:
        pending = _mv.list_pending()
        result["memory_validator"] = {"pending_confirm": len(pending)}
    except Exception as exc:  # noqa: BLE001
        result["memory_validator"] = {
            "pending_confirm": -1,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    # ── 7. Recent SIGTERM count (последние 500 строк лога) ───────────────────
    try:
        log_path = Path("~/.openclaw/krab_runtime_state/krab_main.log").expanduser()
        if log_path.exists():
            proc_result = subprocess.run(  # noqa: S603
                ["tail", "-n", "500", str(log_path)],
                capture_output=True,
                text=True,
                timeout=5,
                env=clean_subprocess_env(),
            )
            result["sigterm_recent_count"] = proc_result.stdout.count("SIGTERM")
        else:
            result["sigterm_recent_count"] = 0
    except Exception as exc:  # noqa: BLE001
        result["sigterm_recent_count"] = -1
        result["sigterm_error"] = str(exc)

    # ── 8. System memory + load average ─────────────────────────────────────
    try:
        vm = psutil.virtual_memory()
        load1, load5, load15 = os.getloadavg()
        result["system"] = {
            "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
            "free_mb": int(vm.available / 1024 / 1024),
            "total_mb": int(vm.total / 1024 / 1024),
            "used_pct": round(vm.percent, 1),
        }
    except Exception as exc:  # noqa: BLE001
        result["system"] = {"error": str(exc), "error_type": type(exc).__name__}

    return result
