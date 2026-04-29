# -*- coding: utf-8 -*-
"""
Сборщик расширенной диагностики Краба — базовая логика для /api/health/deep
и !health deep команды.

Возвращает структурированный dict; форматирование в markdown выполняется
на стороне вызывающего кода (command_handlers, web_app).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ── Дополнительные секции (Session 24) ──────────────────────────────────────


def _collect_sentry() -> dict[str, Any]:
    """Sentry SDK initialization status — после Session 23 init drift fix.

    Возвращает initialized=True если sentry_sdk инициализирован (SDK поднят
    в src/main.py:62 через init_sentry()). dsn_configured=True если SENTRY_DSN
    задан в env (показывает что секрет на месте).
    """
    dsn_set = bool(os.getenv("SENTRY_DSN", "").strip())
    try:
        import sentry_sdk

        # sentry_sdk.is_initialized() — sentry-sdk >= 1.16 (рекомендуемое API).
        # Hub.current.client — fallback для старых версий (deprecated в 2.x).
        try:
            initialized = bool(sentry_sdk.is_initialized())  # type: ignore[attr-defined]
        except AttributeError:
            client = sentry_sdk.Hub.current.client
            initialized = client is not None
        return {
            "initialized": initialized,
            "dsn_configured": dsn_set,
        }
    except ImportError:
        return {
            "initialized": False,
            "dsn_configured": dsn_set,
            "error": "sentry_sdk not installed",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "initialized": False,
            "dsn_configured": dsn_set,
            "error": str(exc)[:120],
            "error_type": type(exc).__name__,
        }


async def _probe_tcp_port(port: int, timeout: float = 1.0) -> dict[str, Any]:
    """TCP probe одного порта на 127.0.0.1. ok=True если открыт connection."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — close-time errors игнорируем
            pass
        return {"port": port, "ok": True}
    except asyncio.TimeoutError:
        return {"port": port, "ok": False, "error": "timeout"}
    except OSError as exc:
        return {"port": port, "ok": False, "error": str(exc)[:60]}


async def _collect_mcp_servers() -> dict[str, Any]:
    """Параллельный probe MCP SSE серверов (8011 yung-nagato, 8012 p0lrd, 8013 hammerspoon)."""
    servers = {
        "yung-nagato": 8011,
        "p0lrd": 8012,
        "hammerspoon": 8013,
    }
    tasks = [_probe_tcp_port(port) for port in servers.values()]
    probe_results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, Any] = {}
    for (name, port), res in zip(servers.items(), probe_results):
        if isinstance(res, Exception):
            out[name] = {"port": port, "ok": False, "error": str(res)[:60]}
        else:
            out[name] = res
    return out


def _collect_cf_tunnel() -> dict[str, Any]:
    """Cloudflare quick tunnel: launchctl status + state файлы.

    State хранится в /tmp/krab_cf_tunnel/{last_url,fail_count}. Tunnel
    управляется LaunchAgent ai.krab.cloudflared-tunnel (ephemeral URL).
    Sentry sync отдельно (DISABLED после Session 23 — Sentry блокирует
    trycloudflare).
    """
    from .subprocess_env import clean_subprocess_env

    label = "ai.krab.cloudflared-tunnel"
    state_dir = Path("/tmp/krab_cf_tunnel")
    loaded = False
    try:
        proc = subprocess.run(  # noqa: S603, S607
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=2,
            env=clean_subprocess_env(),
        )
        loaded = proc.returncode == 0
    except Exception:  # noqa: BLE001
        pass

    last_url: str | None = None
    fail_count: int | None = None
    try:
        url_file = state_dir / "last_url"
        if url_file.exists():
            last_url = url_file.read_text().strip()[:200]
        fc_file = state_dir / "fail_count"
        if fc_file.exists():
            try:
                fail_count = int(fc_file.read_text().strip())
            except ValueError:
                fail_count = -1
    except Exception:  # noqa: BLE001
        pass

    return {
        "label": label,
        "loaded": loaded,
        "last_url": last_url,
        "fail_count": fail_count,
    }


def _collect_error_rate(window_sec: int = 300) -> dict[str, Any]:
    """Errors за последние ``window_sec`` секунд через ring buffer error_handler.

    Источник — `_RECENT_ERROR_TS` deque (FloodWait + RecursionError +
    общие Exception в safe_handler). НЕ парсит лог — pure in-memory счёт.
    """
    try:
        from .error_handler import recent_error_count

        return {
            "errors_5m": int(recent_error_count(window_sec)),
            "window_sec": window_sec,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "errors_5m": -1,
            "window_sec": window_sec,
            "error": str(exc)[:120],
            "error_type": type(exc).__name__,
        }


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
            route_meta["model"] = str(
                get_runtime_primary_model() or getattr(config, "MODEL", "") or "unknown"
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
                    # Загружаем sqlite-vec extension для доступа к vec_chunks.
                    # Используем тот же SQL что memory_doctor.py — vec_chunks.rowid ↔ chunks.id.
                    # NB: vec_chunks_rowids.id всегда NULL (это shadow view с non-standard
                    # semantics), JOIN на c.id = vr.id даёт false positive на ВСЕ строки.
                    import sqlite_vec  # type: ignore[import-not-found]

                    conn.enable_load_extension(True)
                    try:
                        sqlite_vec.load(conn)
                    finally:
                        conn.enable_load_extension(False)
                    vec_orphans = conn.execute(
                        "SELECT COUNT(*) FROM vec_chunks WHERE rowid NOT IN (SELECT id FROM chunks)"
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

    # ── 9. Sentry SDK init (Session 24) ─────────────────────────────────────
    result["sentry"] = _collect_sentry()

    # ── 10. MCP servers TCP probe (Session 24) ──────────────────────────────
    try:
        result["mcp_servers"] = await _collect_mcp_servers()
    except Exception as exc:  # noqa: BLE001
        result["mcp_servers"] = {"error": str(exc)[:120], "error_type": type(exc).__name__}

    # ── 11. Cloudflare quick tunnel (Session 24) ────────────────────────────
    result["cf_tunnel"] = _collect_cf_tunnel()

    # ── 12. Error rate за 5 минут (Session 24) ──────────────────────────────
    result["error_rate_5m"] = _collect_error_rate(window_sec=300)

    return result
