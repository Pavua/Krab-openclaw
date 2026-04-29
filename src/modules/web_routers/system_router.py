# -*- coding: utf-8 -*-
"""
System router — Phase 2 Wave Y + Wave AA extraction (Session 25).

Объединяет runtime/stats/system endpoints, агрегирующих состояние
Краба для Dashboard V4 и diagnostics:

- GET  /api/runtime/operator-profile   — machine-readable профиль учётки/runtime
- GET  /api/runtime/summary            — единый summary (health/route/costs/swarm/...)
- GET  /api/dashboard/summary          — Dashboard V4 агрегатор (15 источников в один)
- GET  /api/stats                      — router/black_box/rag stats
- GET  /api/stats/caches               — chat_ban/capability/voice cache counts
- GET  /api/system/diagnostics         — RAM/CPU/budget/local LLM diagnostics
- POST /api/runtime/chat-session/clear — Wave AA: очистка runtime chat-session
- POST /api/runtime/repair-active-shared-permissions — Wave QQ: нормализация прав в `Краб-active`
- POST /api/runtime/recover — Wave QQ: recovery playbook (repair + sync + tier/probe)
- GET  /api/runtime/handoff — Wave UU: единый runtime-снимок для anti-413 миграции
- POST /api/krab/restart_userbot — Wave SS: перезапуск userbot c rate-limit (legacy watchdog)

Helper-методы WebApp (``_runtime_operator_profile``,
``_build_stats_router_payload``, ``_resolve_local_runtime_truth``)
инжектируются через ``ctx.deps`` factory'ем ``_make_router_context``.
Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from ._context import RouterContext


def build_system_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с runtime/stats/system endpoints."""
    router = APIRouter(tags=["system"])

    # ── /api/runtime/operator-profile ───────────────────────────────────────

    @router.get("/api/runtime/operator-profile")
    async def runtime_operator_profile() -> dict:
        """Profile текущей учётки/runtime для multi-account handoff."""
        helper = ctx.get_dep("runtime_operator_profile_helper")
        profile = helper() if callable(helper) else {}
        return {"ok": True, "profile": profile}

    # ── /api/runtime/summary ────────────────────────────────────────────────

    @router.get("/api/runtime/summary")
    async def runtime_summary() -> dict:
        """Единый summary endpoint — полное состояние Краба одним запросом."""
        from ...config import config
        from ...core.cost_analytics import cost_analytics as _ca
        from ...core.silence_mode import silence_manager
        from ...core.swarm_task_board import swarm_task_board
        from ...core.swarm_team_listener import is_listeners_enabled
        from ...openclaw_client import openclaw_client as _oc

        try:
            health = await ctx.collect_runtime_lite()
        except Exception:  # noqa: BLE001
            health = {}

        kraab = ctx.get_dep("kraab_userbot")
        translator_profile: Any = {}
        translator_session: Any = {}
        if kraab is not None:
            try:
                translator_profile = kraab.get_translator_runtime_profile()
            except Exception:  # noqa: BLE001
                translator_profile = {}
            try:
                translator_session = kraab.get_translator_session_state()
            except Exception:  # noqa: BLE001
                translator_session = {}

        return {
            "ok": True,
            "health": health,
            "route": _oc.get_last_runtime_route(),
            "costs": _ca.build_usage_report_dict(),
            "translator": {
                "profile": translator_profile,
                "session": translator_session,
            },
            "swarm": {
                "task_board": swarm_task_board.get_board_summary(),
                "listeners_enabled": is_listeners_enabled(),
            },
            "silence": silence_manager.status(),
            "notify_enabled": bool(getattr(config, "TOOL_NARRATION_ENABLED", True)),
        }

    # ── /api/dashboard/summary ──────────────────────────────────────────────

    @router.get("/api/dashboard/summary")
    async def dashboard_summary() -> dict:
        """Dashboard V4 aggregator — один запрос вместо 15."""
        from ...core.dashboard_summary import collect_dashboard_summary_async

        boot = ctx.get_boot_ts()
        router_dep = ctx.get_dep("router")
        return await collect_dashboard_summary_async(boot_ts=boot, router=router_dep)

    # ── /api/stats ──────────────────────────────────────────────────────────

    @router.get("/api/stats")
    async def get_stats() -> dict:
        """Главный stats endpoint (router/black_box/rag)."""
        router_dep = ctx.deps["router"]
        black_box = ctx.get_dep("black_box")
        rag = getattr(router_dep, "rag", None)

        builder = ctx.get_dep("build_stats_router_payload_helper")
        if callable(builder):
            router_payload = await builder(router_dep)
        else:
            router_payload = {}

        return {
            "router": router_payload,
            "black_box": black_box.get_stats()
            if black_box and hasattr(black_box, "get_stats")
            else {"enabled": False},
            "rag": rag.get_stats()
            if rag and hasattr(rag, "get_stats")
            else {"enabled": False, "count": 0},
        }

    # ── /api/stats/caches ───────────────────────────────────────────────────

    @router.get("/api/stats/caches")
    async def get_stats_caches() -> dict:
        """Aggregated cache-метрики для /stats dashboard."""
        try:
            from ...core.chat_ban_cache import chat_ban_cache as _cbc

            ban_entries = _cbc.list_entries()
            ban_count = len(ban_entries)
        except Exception:  # noqa: BLE001
            ban_count = 0

        try:
            from ...core.chat_capability_cache import chat_capability_cache as _ccc

            cap_entries = _ccc.list_entries()
            cap_count = len(cap_entries)
            voice_disallowed = sum(1 for e in cap_entries if e.get("voice_allowed") is False)
            slow_mode = sum(
                1
                for e in cap_entries
                if isinstance(e.get("slow_mode_seconds"), (int, float))
                and e["slow_mode_seconds"] > 0
            )
        except Exception:  # noqa: BLE001
            cap_count = 0
            voice_disallowed = 0
            slow_mode = 0

        try:
            userbot = ctx.get_dep("kraab_userbot")
            blocked = userbot.get_voice_blocked_chats() if userbot else []
            voice_blocked_count = len(blocked)
        except Exception:  # noqa: BLE001
            voice_blocked_count = 0

        return {
            "ban_cache_count": ban_count,
            "capability_cache_count": cap_count,
            "voice_blocked_count": voice_blocked_count,
            "capability_voice_disallowed": voice_disallowed,
            "capability_slow_mode": slow_mode,
        }

    # ── /api/system/diagnostics ─────────────────────────────────────────────

    @router.get("/api/system/diagnostics")
    async def system_diagnostics() -> dict:
        """[R11] Глубокая диагностика сервера (RAM/CPU/Бюджет/Local LLM)."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.get_dep("router")
        if not router_dep:
            return {"ok": False, "error": "router_not_found"}

        health_service = ctx.get_dep("health_service")
        if not health_service:
            health_service = EcosystemHealthService(router=router_dep)

        health_data = await health_service.collect()

        truth_helper = ctx.get_dep("resolve_local_runtime_truth_helper")
        if callable(truth_helper):
            local_truth = await truth_helper(router_dep)
        else:
            local_truth = {}

        status = "ok"
        if not bool(local_truth.get("runtime_reachable")):
            status = "degraded"
            if getattr(router_dep, "active_tier", "") == "default":
                status = "failed"
        elif getattr(router_dep, "active_tier", "") == "paid":
            status = "degraded"

        watchdog_dep = ctx.get_dep("watchdog")
        return {
            "ok": True,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resources": health_data.get("resources", {}),
            "budget": health_data.get("budget", {}),
            "local_ai": {
                "engine": local_truth.get("engine", getattr(router_dep, "local_engine", "unknown")),
                "model": local_truth.get("active_model", ""),
                "available": bool(local_truth.get("runtime_reachable")),
                "loaded_models": local_truth.get("loaded_models", []),
            },
            "watchdog": {
                "last_recoveries": getattr(watchdog_dep, "last_recovery_attempt", {})
                if watchdog_dep
                else {}
            },
        }

    # ── /api/runtime/chat-session/clear (Wave AA) ───────────────────────────

    @router.post("/api/runtime/chat-session/clear")
    async def runtime_chat_session_clear(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Очищает runtime chat-session по chat_id через owner-only web endpoint.

        Зачем нужно:
        - `!clear` в Telegram требует ручного сообщения из owner-чата;
        - для recover/handoff/ops нужен тот же эффект из owner panel/CLI без
          похода в Telegram;
        - endpoint чистит и in-memory историю, и persisted `history_cache.db`
          через общий `openclaw_client.clear_session`, не дублируя логику.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        data = payload or {}
        chat_id = str(data.get("chat_id") or "").strip()
        if not chat_id:
            raise HTTPException(status_code=400, detail="chat_id_required")

        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw or not hasattr(openclaw, "clear_session"):
            raise HTTPException(status_code=503, detail="chat_session_clear_not_supported")

        note = str(data.get("note") or "").strip()
        try:
            openclaw.clear_session(chat_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"chat_session_clear_failed: {exc}"
            ) from exc

        runtime_after = await ctx.collect_runtime_lite()
        return {
            "ok": True,
            "action": "clear_chat_session",
            "chat_id": chat_id,
            "note": note,
            "runtime_after": runtime_after,
        }

    # ── /api/runtime/repair-active-shared-permissions (Wave QQ) ─────────────

    @router.post("/api/runtime/repair-active-shared-permissions")
    async def runtime_repair_active_shared_permissions(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Нормализует group-write права в `Краб-active` через owner web-key."""
        ctx.assert_write_access(x_krab_web_key, token)

        active_shared_root_helper = ctx.get_dep("active_shared_root_helper")
        normalize_helper = ctx.get_dep("normalize_shared_worktree_permissions_helper")
        permission_health_helper = ctx.get_dep("active_shared_permission_health_helper")

        active_shared_root = (
            active_shared_root_helper() if callable(active_shared_root_helper) else None
        )
        repair_summary = (
            normalize_helper(active_shared_root)
            if callable(normalize_helper) and active_shared_root is not None
            else {"ok": False, "error": "normalize_helper_unavailable"}
        )
        permission_health = permission_health_helper() if callable(permission_health_helper) else {}
        return {
            "ok": bool(repair_summary.get("ok")),
            "repair": repair_summary,
            "active_shared_permission_health": permission_health,
        }

    # ── /api/runtime/recover (Wave QQ) ──────────────────────────────────────

    @router.post("/api/runtime/recover")
    async def runtime_recover(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Безопасный recovery-плейбук для runtime-контуров.

        Что делает:
        1) `openclaw_runtime_repair.command` (по умолчанию включен),
        2) `sync_openclaw_models.command` (по умолчанию включен),
        3) optional manual tier switch (`force_tier=free|paid`),
        4) optional cloud runtime probe (`probe_cloud_runtime=true`),
        5) возвращает post-check снимок.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        data = payload or {}

        bool_env_fn = ctx.get_dep("bool_env_helper")

        def _to_bool(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if callable(bool_env_fn):
                return bool_env_fn(str(value), default)
            return default

        run_repair = _to_bool(data.get("run_openclaw_runtime_repair", True), True)
        run_sync = _to_bool(data.get("run_sync_openclaw_models", True), True)
        probe_cloud = _to_bool(data.get("probe_cloud_runtime", False), False)
        force_tier = str(data.get("force_tier", "") or "").strip().lower()

        run_script = ctx.get_dep("run_project_python_script_helper")
        project_root = ctx.project_root

        steps: list[dict[str, Any]] = []

        if run_repair:
            if callable(run_script):
                repair_result = run_script(
                    project_root / "scripts" / "openclaw_runtime_repair.py",
                    timeout_seconds=120,
                )
            else:
                repair_result = {
                    "ok": False,
                    "exit_code": 1,
                    "error": "run_script_helper_unavailable",
                    "stdout_tail": "",
                }
            steps.append(
                {
                    "step": "openclaw_runtime_repair",
                    "ok": bool(repair_result.get("ok")),
                    "exit_code": int(repair_result.get("exit_code", 1)),
                    "error": str(repair_result.get("error") or ""),
                    "stdout_tail": str(repair_result.get("stdout_tail") or ""),
                }
            )
        else:
            steps.append({"step": "openclaw_runtime_repair", "ok": True, "skipped": True})

        if run_sync:
            if callable(run_script):
                sync_result = run_script(
                    project_root / "scripts" / "sync_openclaw_models.py",
                    timeout_seconds=120,
                )
            else:
                sync_result = {
                    "ok": False,
                    "exit_code": 1,
                    "error": "run_script_helper_unavailable",
                    "stdout_tail": "",
                }
            steps.append(
                {
                    "step": "sync_openclaw_models",
                    "ok": bool(sync_result.get("ok")),
                    "exit_code": int(sync_result.get("exit_code", 1)),
                    "error": str(sync_result.get("error") or ""),
                    "stdout_tail": str(sync_result.get("stdout_tail") or ""),
                }
            )
        else:
            steps.append({"step": "sync_openclaw_models", "ok": True, "skipped": True})

        openclaw = ctx.get_dep("openclaw_client")
        if force_tier in {"free", "paid"}:
            if not openclaw or not hasattr(openclaw, "switch_cloud_tier"):
                steps.append(
                    {
                        "step": "switch_cloud_tier",
                        "ok": False,
                        "error": "switch_cloud_tier_not_supported",
                        "requested_tier": force_tier,
                    }
                )
            else:
                try:
                    tier_result = await openclaw.switch_cloud_tier(force_tier)
                    steps.append(
                        {
                            "step": "switch_cloud_tier",
                            "ok": bool(tier_result.get("ok")),
                            "requested_tier": force_tier,
                            "result": tier_result,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    steps.append(
                        {
                            "step": "switch_cloud_tier",
                            "ok": False,
                            "requested_tier": force_tier,
                            "error": str(exc),
                        }
                    )

        cloud_runtime: dict[str, Any] | None = None
        if probe_cloud:
            if not openclaw or not hasattr(openclaw, "get_cloud_runtime_check"):
                cloud_runtime = {
                    "available": False,
                    "error": "cloud_runtime_check_not_supported",
                }
            else:
                try:
                    probe = await asyncio.wait_for(openclaw.get_cloud_runtime_check(), timeout=18.0)
                    cloud_runtime = {"available": True, "report": probe}
                except asyncio.TimeoutError:
                    cloud_runtime = {"available": False, "error": "timeout"}
                except Exception as exc:  # noqa: BLE001
                    cloud_runtime = {"available": False, "error": str(exc)}

        runtime_after = await ctx.collect_runtime_lite()
        ok = all(bool(item.get("ok")) for item in steps)
        return {
            "ok": ok,
            "steps": steps,
            "runtime_after": runtime_after,
            "cloud_runtime": cloud_runtime,
        }

    # ── /api/session10/summary (Wave TT) ────────────────────────────────────

    @router.get("/api/session10/summary")
    async def session10_summary() -> dict:
        """
        Aggregated Session 10 features stats для V4 Dashboard Hub.

        Делает один запрос вместо N — фронтенду не нужно дёргать
        отдельные endpoints для memory_validator/archive/chrome/restart/observability.

        Каждая секция обёрнута в try/except: при отсутствии модуля возвращаем
        defaults, endpoint не падает в 500.
        """
        import structlog

        from ...core.ecosystem_health import EcosystemHealthService

        _logger = structlog.get_logger("system_router")

        # ── session_info (статический + dynamic commits через git) ─────
        session_info: dict[str, Any] = {
            "name": "Session 10",
            "date": "2026-04-17",
            "status": "closed",
            "new_tests_count": 155,
        }
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "--since=1 day", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if result.returncode == 0:
                session_info["commits_count"] = int(result.stdout.strip() or 0)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("session10_commits_count_failed", error=str(exc))

        # ── memory_validator (модуль пока не существует → defaults) ─────
        memory_validator: dict[str, Any] = {
            "enabled": False,
            "safe_total": 0,
            "injection_blocked_total": 0,
            "confirmed_total": 0,
            "confirm_failed_total": 0,
            "pending_count": 0,
        }
        try:
            from ...core import memory_validator as _mv  # type: ignore[attr-defined]

            if hasattr(_mv, "get_validator_stats"):
                mv_stats = _mv.get_validator_stats()
                if isinstance(mv_stats, dict):
                    memory_validator.update(mv_stats)
                    memory_validator.setdefault("enabled", True)
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            _logger.warning("session10_memory_validator_failed", error=str(exc))

        # ── memory_archive (archive.db file-level stats + indexer state) ─
        memory_archive: dict[str, Any] = {
            "exists": False,
            "size_bytes": 0,
            "size_mb": 0.0,
            "message_count": 0,
            "chats_count": 0,
            "chunks_count": 0,
            "indexer_state": "stopped",
        }
        try:
            from ...core.memory_archive import DEFAULT_ARCHIVE_PATH

            archive_path = DEFAULT_ARCHIVE_PATH
            if archive_path.exists():
                size_bytes = archive_path.stat().st_size
                memory_archive["exists"] = True
                memory_archive["size_bytes"] = size_bytes
                memory_archive["size_mb"] = round(size_bytes / (1024 * 1024), 2)
                try:
                    conn = sqlite3.connect(f"file:{archive_path}?mode=ro", uri=True)
                    try:
                        cursor = conn.cursor()
                        for table, key in (
                            ("messages", "message_count"),
                            ("chats", "chats_count"),
                            ("chunks", "chunks_count"),
                        ):
                            try:
                                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                                memory_archive[key] = int(cursor.fetchone()[0])
                            except sqlite3.Error:
                                pass
                    finally:
                        conn.close()
                except sqlite3.Error as exc:
                    _logger.debug("session10_archive_query_failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("session10_memory_archive_failed", error=str(exc))

        # Переиспользуем existing helper (late-bound через ctx) для indexer state.
        indexer_helper = ctx.get_dep("memory_indexer_state_helper")
        memory_archive["indexer_state"] = (
            indexer_helper() if callable(indexer_helper) else "stopped"
        )

        # ── dedicated_chrome (env-driven; модуль пока optional) ─────────
        dedicated_chrome: dict[str, Any] = {
            "enabled": bool(os.environ.get("KRAB_DEDICATED_CHROME_ENABLED", "") == "1"),
            "running": False,
            "port": int(os.environ.get("KRAB_DEDICATED_CHROME_PORT") or 9222),
        }
        try:
            probe_url = f"http://127.0.0.1:{dedicated_chrome['port']}/json/version"
            async with httpx.AsyncClient(timeout=0.5) as probe_client:
                probe_resp = await probe_client.get(probe_url)
                dedicated_chrome["running"] = probe_resp.status_code == 200
        except Exception:  # noqa: BLE001
            pass

        # ── auto_restart (services_tracked + attempts из watchdog deps) ──
        auto_restart: dict[str, Any] = {
            "enabled": False,
            "services_tracked": [],
            "total_attempts_last_hour": 0,
        }
        try:
            watchdog = ctx.get_dep("watchdog")
            if watchdog is not None:
                auto_restart["enabled"] = True
                recoveries = getattr(watchdog, "last_recovery_attempt", {}) or {}
                if isinstance(recoveries, dict):
                    auto_restart["services_tracked"] = sorted(recoveries.keys())
                    cutoff = time.time() - 3600
                    attempts = 0
                    for rec in recoveries.values():
                        if isinstance(rec, dict):
                            ts = rec.get("ts") or rec.get("timestamp") or 0
                            if isinstance(ts, (int, float)) and ts >= cutoff:
                                attempts += 1
                    auto_restart["total_attempts_last_hour"] = attempts
        except Exception as exc:  # noqa: BLE001
            _logger.warning("session10_auto_restart_failed", error=str(exc))

        # ── observability (env-driven флаги + stagnation threshold) ─────
        try:
            stagnation_threshold = int(os.environ.get("LLM_STAGNATION_THRESHOLD_SEC") or 120)
        except (TypeError, ValueError):
            stagnation_threshold = 120

        observability = {
            "correlation_id_active": True,
            "tool_indicator_enabled": True,
            "stagnation_threshold_sec": stagnation_threshold,
        }

        new_commands = [
            {
                "name": "!confirm",
                "description": "Подтвердить persistent memory write (owner)",
            },
            {
                "name": "!reset",
                "description": "Aggressive очистка 4 слоёв истории",
            },
            {
                "name": "!memory stats",
                "description": "Memory Layer статистика",
            },
        ]

        # ── known_issues (резолв по commit hash'ам; конфиг через env) ──
        known_issues: list[str] = []
        if os.environ.get("KRAB_KNOWN_ISSUE_CHROME_PROMPTS") == "1":
            known_issues.append("chrome_prompts_from_extension")

        # ── session_12 (Wave 16 Chado-inspired modules) ──────────────────
        health_svc = ctx.get_dep("health_service")
        if health_svc is None:
            router_dep = ctx.get_dep("router")
            if router_dep is not None:
                health_svc = EcosystemHealthService(router=router_dep)
        session_12: dict[str, Any] = {}
        if health_svc is not None:
            try:
                session_12 = health_svc._collect_session_12_stats()
            except Exception as exc:  # noqa: BLE001
                _logger.debug("session12_stats_failed", error=str(exc))

        return {
            "ok": True,
            "generated_at": int(time.time()),
            "session_info": session_info,
            "memory_validator": memory_validator,
            "memory_archive": memory_archive,
            "new_commands": new_commands,
            "dedicated_chrome": dedicated_chrome,
            "auto_restart": auto_restart,
            "observability": observability,
            "known_issues": known_issues,
            "session_12": session_12,
        }

    # ── /api/runtime/handoff (Wave UU) ──────────────────────────────────────

    @router.get("/api/runtime/handoff")
    async def runtime_handoff(probe_cloud_runtime: str = Query(default="1")) -> dict:
        """
        Единый runtime-снимок для безопасной миграции в новый чат (Anti-413).

        Формат intentionally machine-readable, чтобы его можно было:
        - сохранить в артефакты;
        - приложить в новый диалог без ручной реконструкции контекста.
        """
        # Reuse module-level reference в src.modules.web_app — это единая точка,
        # которую существующие тесты монкей-патчат через
        # ``monkeypatch.setattr("src.modules.web_app.inbox_service", ...)``.
        # Прямой импорт `from ...core.inbox_service import inbox_service` ломал бы
        # эти тесты.
        import sys as _sys

        _wam = _sys.modules.get("src.modules.web_app")
        if _wam is not None and hasattr(_wam, "inbox_service"):
            _inbox_service = getattr(_wam, "inbox_service")
        else:
            from ...core.inbox_service import inbox_service as _inbox_service

        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")

        runtime_lite = await ctx.collect_runtime_lite()

        operator_profile_helper = ctx.get_dep("runtime_operator_profile_helper")
        operator_profile = operator_profile_helper() if callable(operator_profile_helper) else {}

        translator_snapshot_helper = ctx.get_dep("translator_readiness_snapshot")
        translator_snapshot: dict[str, Any] = {}
        if callable(translator_snapshot_helper):
            translator_snapshot = await translator_snapshot_helper(runtime_lite=runtime_lite) or {}

        capability_registry_helper = ctx.get_dep("capability_registry_snapshot_helper")
        capability_registry: dict[str, Any] = {}
        if callable(capability_registry_helper):
            capability_registry = await capability_registry_helper(runtime_lite=runtime_lite) or {}

        safe_health_helper = ctx.get_dep("runtime_handoff_safe_client_health_helper")

        async def _health(client: Any, source: str) -> dict[str, Any]:
            if not callable(safe_health_helper):
                return {"ok": False, "status": "not_supported", "source": source, "detail": {}}
            return await safe_health_helper(client, source=source, timeout_sec=3.0)

        openclaw_health = await _health(openclaw, "openclaw")
        voice_health = await _health(voice_gateway, "voice_gateway")
        krab_ear_health = await _health(krab_ear, "krab_ear")

        bool_env_helper = ctx.get_dep("bool_env_helper")
        if callable(bool_env_helper):
            should_probe_cloud_runtime = bool_env_helper(str(probe_cloud_runtime or "1"), True)
        else:
            should_probe_cloud_runtime = str(probe_cloud_runtime or "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        cloud_runtime: dict[str, Any]
        if not should_probe_cloud_runtime:
            cloud_runtime = {"available": False, "skipped": True, "reason": "probe_disabled"}
        elif openclaw and hasattr(openclaw, "get_cloud_runtime_check"):
            try:
                cloud_report = await asyncio.wait_for(
                    openclaw.get_cloud_runtime_check(), timeout=18.0
                )
                cloud_runtime = {"available": True, "report": cloud_report}
                # После cloud-probe `openclaw_client` может обновить tier/auth truth.
                # Переснимаем lightweight runtime, чтобы handoff не уносил stale
                # `configured/free` сразу после restart, когда probe уже увидел real state.
                runtime_lite = await ctx.collect_runtime_lite(force_refresh=True)
            except asyncio.TimeoutError:
                cloud_runtime = {"available": False, "error": "timeout"}
            except Exception as exc:  # noqa: BLE001
                cloud_runtime = {"available": False, "error": str(exc)}
        else:
            cloud_runtime = {"available": False, "error": "not_supported"}

        latest_path_helper = ctx.get_dep("runtime_handoff_latest_path_by_glob_helper")
        if callable(latest_path_helper):
            latest_bundle = latest_path_helper("artifacts/handoff_*")
            latest_checkpoint = latest_path_helper("artifacts/context_checkpoints/checkpoint_*.md")
            latest_pack_dir = latest_path_helper("artifacts/context_transition/pack_*")
        else:
            latest_bundle = None
            latest_checkpoint = None
            latest_pack_dir = None

        latest_transfer_prompt = (
            str(latest_pack_dir / "TRANSFER_PROMPT_RU.md")
            if latest_pack_dir and (latest_pack_dir / "TRANSFER_PROMPT_RU.md").exists()
            else None
        )

        operator_workflow = _inbox_service.get_workflow_snapshot()

        git_helper = ctx.get_dep("runtime_handoff_git_snapshot_helper")
        git_snapshot = git_helper() if callable(git_helper) else {}

        mask_helper = ctx.get_dep("runtime_handoff_mask_secret_helper")

        def _mask(value: str) -> str:
            return mask_helper(value) if callable(mask_helper) else ""

        return {
            "ok": True,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_root": str(ctx.project_root),
            "git": git_snapshot,
            "health_lite": {
                "ok": True,
                "status": "up",
                "telegram_session_state": runtime_lite.get("telegram_session_state"),
                "lmstudio_model_state": runtime_lite.get("lmstudio_model_state"),
                "openclaw_auth_state": runtime_lite.get("openclaw_auth_state"),
                "workspace_attached": bool(
                    (
                        (runtime_lite.get("workspace_state") or {})
                        if isinstance(runtime_lite, dict)
                        else {}
                    ).get("shared_workspace_attached")
                ),
                "last_runtime_route": runtime_lite.get("last_runtime_route"),
                "inbox_summary": operator_workflow.get("summary")
                or runtime_lite.get("inbox_summary"),
            },
            "runtime": runtime_lite,
            "inbox_summary": operator_workflow.get("summary") or {},
            "operator_workflow": operator_workflow,
            "operator_profile": operator_profile,
            "capability_registry_summary": capability_registry.get("summary") or {},
            "policy_matrix_summary": (capability_registry.get("policy_matrix") or {}).get("summary")
            or {},
            "channel_capabilities_summary": (
                (capability_registry.get("contours") or {}).get("channels", {}).get("summary") or {}
            ),
            "translator_readiness": translator_snapshot,
            "services": {
                "openclaw": openclaw_health,
                "voice_gateway": voice_health,
                "krab_ear": krab_ear_health,
            },
            "cloud_runtime": cloud_runtime,
            "masked_secrets": {
                "openclaw_token": _mask(
                    os.getenv(
                        "OPENCLAW_GATEWAY_TOKEN",
                        os.getenv("OPENCLAW_TOKEN", os.getenv("OPENCLAW_API_KEY", "")),
                    )
                ),
                "web_api_key": _mask(os.getenv("WEB_API_KEY", "")),
                "gemini_free": _mask(os.getenv("GEMINI_API_KEY_FREE", "")),
                "gemini_paid": _mask(os.getenv("GEMINI_API_KEY_PAID", "")),
                "openai_api_key": _mask(os.getenv("OPENAI_API_KEY", "")),
            },
            "artifacts": {
                "latest_handoff_bundle_dir": str(latest_bundle) if latest_bundle else None,
                "latest_context_checkpoint": str(latest_checkpoint) if latest_checkpoint else None,
                "latest_transition_pack_dir": str(latest_pack_dir) if latest_pack_dir else None,
                "latest_transfer_prompt": latest_transfer_prompt,
                "master_plan_doc": str(ctx.project_root / "docs" / "MASTER_PLAN_VNEXT_RU.md"),
                "translator_audit_doc": str(
                    ctx.project_root / "docs" / "CALL_TRANSLATOR_AUDIT_RU.md"
                ),
                "multi_account_doc": str(
                    ctx.project_root / "docs" / "MULTI_ACCOUNT_SWITCHOVER_RU.md"
                ),
                "parallel_dialog_doc": str(
                    ctx.project_root / "docs" / "PARALLEL_DIALOG_PROTOCOL_RU.md"
                ),
            },
        }

    # ── /api/krab/restart_userbot (Wave SS) ─────────────────────────────────

    @router.post("/api/krab/restart_userbot")
    async def restart_userbot(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Перезапускает только Telegram userbot без полного runtime switchover.

        Зачем нужен отдельный endpoint:
        - legacy watchdog уже умеет дёргать именно этот маршрут;
        - перезапуск userbot легче и безопаснее, чем полный restart всего Krab;
        - закрывает split-state, когда web panel жива, а userbot деградировал.
        """
        import structlog

        _logger = structlog.get_logger("system_router")

        # W32: лог caller (IP + User-Agent) чтобы поймать restart-loop источник.
        client_host = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "n/a")
        referer = request.headers.get("referer", "n/a")
        _logger.warning(
            "restart_userbot_endpoint_called",
            client_ip=client_host,
            user_agent=user_agent[:120],
            referer=referer[:120],
        )

        # W32 v3: rate limit — max 1 restart per 5 min. Защита от restart loop.
        import time as _time

        get_last_ts = ctx.get_dep("restart_userbot_get_last_ts_helper")
        set_last_ts = ctx.get_dep("restart_userbot_set_last_ts_helper")

        now_ts = _time.time()
        last_restart_ts = float(get_last_ts() if callable(get_last_ts) else 0) or 0.0
        cooldown_sec = 300
        if now_ts - last_restart_ts < cooldown_sec:
            remaining = int(cooldown_sec - (now_ts - last_restart_ts))
            _logger.warning(
                "restart_userbot_rate_limited",
                client_ip=client_host,
                cooldown_remaining_sec=remaining,
            )
            return {
                "ok": False,
                "error": "rate_limited",
                "detail": f"cooldown {remaining}s (max 1 per {cooldown_sec}s)",
            }
        if callable(set_last_ts):
            set_last_ts(now_ts)

        ctx.assert_write_access(x_krab_web_key, token)
        kraab_userbot = ctx.get_dep("kraab_userbot")
        if (
            not kraab_userbot
            or not hasattr(kraab_userbot, "start")
            or not hasattr(kraab_userbot, "stop")
        ):
            return {
                "ok": False,
                "error": "userbot_restart_unavailable",
                "detail": "kraab_userbot не поддерживает start/stop для restart endpoint",
            }

        before_state: dict = {}
        if hasattr(kraab_userbot, "get_runtime_state"):
            try:
                before_state = dict(kraab_userbot.get_runtime_state() or {})
            except Exception:  # noqa: BLE001
                before_state = {}

        try:
            if hasattr(kraab_userbot, "restart"):
                await kraab_userbot.restart(reason="web_api_restart_userbot")
            else:
                await kraab_userbot.stop()
                await kraab_userbot.start()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("runtime_restart_userbot_failed", error=str(exc))
            return {
                "ok": False,
                "error": "restart_failed",
                "detail": str(exc),
                "before": before_state,
            }

        after_state: dict = {}
        if hasattr(kraab_userbot, "get_runtime_state"):
            try:
                after_state = dict(kraab_userbot.get_runtime_state() or {})
            except Exception:  # noqa: BLE001
                after_state = {}

        return {
            "ok": True,
            "action": "restart_userbot",
            "before": before_state,
            "after": after_state,
        }

    return router
