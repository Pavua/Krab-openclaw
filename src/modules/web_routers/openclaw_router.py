# -*- coding: utf-8 -*-
"""
OpenClaw router — Phase 2 Wave M+N extraction (Session 25).

OpenClaw endpoints через RouterContext. Только endpoints, которые работают
исключительно через ``ctx.get_dep("openclaw_client")`` / ``ctx.project_root``
без вызовов WebApp helper-методов (``_collect_openclaw_*_snapshot``,
``_load_openclaw_runtime_config``, ``_run_openclaw_cli`` и пр.) и без мутации
``_runtime_lite_cache``.

Endpoints (Wave M, GET):
- GET /api/openclaw/report             — health-report OpenClaw
- GET /api/openclaw/deep-check         — расширенная проверка
- GET /api/openclaw/remediation-plan   — план исправлений
- GET /api/openclaw/cloud/tier/state   — диагностика Cloud Tier State

Endpoints (Wave N, POST через ctx.assert_write_access):
- POST /api/openclaw/cloud/tier/reset           — сброс tier на free
- POST /api/openclaw/channels/runtime-repair    — запуск repair-скрипта
- POST /api/openclaw/channels/signal-guard-run  — однократный Signal Guard

Endpoints (Wave DD, GET через helper injection):
- GET /api/openclaw/cron/status        — через `openclaw_cron_snapshot_helper`
- GET /api/openclaw/cron/jobs          — через `openclaw_cron_snapshot_helper`
- GET /api/openclaw/runtime-config     — через `openclaw_runtime_config_snapshot_helper`

Endpoints (Wave EE, GET через helper injection):
- GET /api/openclaw/model-routing/status — через `openclaw_model_routing_helper`
                                            + `openclaw_model_routing_overlay_helper`
                                            + `openclaw_client.get_last_runtime_route`
- GET /api/openclaw/model-compat/probe   — через `openclaw_model_compat_probe_helper`
- GET /api/openclaw/model-autoswitch/status — через `openclaw_model_autoswitch_helper`

SKIP (HARD, требуют helper promote):
- /api/openclaw/cron/jobs/{create,toggle,remove,run_now} — `_run_openclaw_cli`
- /api/openclaw/channels/status        — `_collect_openclaw_channels_snapshot`
- /api/openclaw/cloud, /api/openclaw/cloud/diagnostics — `_openclaw_cloud_diagnostics_impl`
- /api/openclaw/cloud/runtime-check    — мутирует `self._runtime_lite_cache`
- /api/openclaw/cloud/switch-tier      — мутирует `self._runtime_lite_cache`

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
import inspect
import sys

from fastapi import APIRouter, Header, HTTPException, Query

from src.core.observability import build_ops_response

from ._context import RouterContext


def build_openclaw_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с openclaw GET endpoints."""
    router = APIRouter(tags=["openclaw"])

    # ---------- GET /api/openclaw/report ----------------------------------
    @router.get("/api/openclaw/report")
    async def openclaw_report() -> dict:
        """Агрегированный health-report OpenClaw."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_health_report"):
            return {"available": False, "error": "openclaw_report_not_supported"}
        try:
            report = await openclaw.get_health_report()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": "openclaw_report_failed", "detail": str(exc)}
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/deep-check ------------------------------
    @router.get("/api/openclaw/deep-check")
    async def openclaw_deep_check() -> dict:
        """Расширенная проверка OpenClaw (включая tool smoke и remediation)."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_deep_health_report"):
            return {"available": False, "error": "openclaw_deep_check_not_supported"}
        try:
            report = await openclaw.get_deep_health_report()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "error": "openclaw_deep_check_failed",
                "detail": str(exc),
            }
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/remediation-plan ------------------------
    @router.get("/api/openclaw/remediation-plan")
    async def openclaw_remediation_plan() -> dict:
        """Пошаговый план исправления OpenClaw контуров."""
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_remediation_plan"):
            return {"available": False, "error": "openclaw_remediation_not_supported"}
        try:
            report = await openclaw.get_remediation_plan()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "error": "openclaw_remediation_failed",
                "detail": str(exc),
            }
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/cloud/tier/state ------------------------
    @router.get("/api/openclaw/cloud/tier/state")
    async def openclaw_cloud_tier_state() -> dict:
        """[R23/R25] Диагностика Cloud Tier State.

        Возвращает текущий активный tier (free/paid/default), статистику
        переключений, метрики (cloud_attempts_total и др.) и конфигурацию.
        Не содержит секретов — только счётчики событий.
        """
        try:
            openclaw = ctx.get_dep("openclaw_client")
            if not openclaw:
                return build_ops_response(
                    status="failed",
                    error_code="openclaw_client_not_configured",
                    summary="Openclaw client not configured",
                )
            if not hasattr(openclaw, "get_tier_state_export"):
                return build_ops_response(
                    status="failed",
                    error_code="tier_state_not_supported",
                    summary="Tier state not supported",
                )
            tier_state = openclaw.get_tier_state_export()
            return build_ops_response(status="ok", data={"tier_state": tier_state})
        except Exception as exc:  # noqa: BLE001
            return build_ops_response(status="failed", error_code="system_error", summary=str(exc))

    # ---------- POST /api/openclaw/cloud/tier/reset (Wave N) -------------
    @router.post("/api/openclaw/cloud/tier/reset")
    async def openclaw_cloud_tier_reset(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """[R23/R25] Ручной сброс Cloud Tier на free.

        Требует X-Krab-Web-Key или token (WEB_API_KEY).
        Снимает sticky_paid флаг, не требует перезапуска бота.
        Возвращает: {ok, previous_tier, new_tier, reset_at}.
        """
        try:
            ctx.assert_write_access(x_krab_web_key, token)
        except HTTPException as http_exc:
            return build_ops_response(
                status="failed", error_code="forbidden", summary=http_exc.detail
            )

        try:
            openclaw = ctx.get_dep("openclaw_client")
            if not openclaw:
                return build_ops_response(
                    status="failed",
                    error_code="openclaw_client_not_configured",
                    summary="Openclaw client not configured",
                )
            if not hasattr(openclaw, "reset_cloud_tier"):
                return build_ops_response(
                    status="failed",
                    error_code="tier_reset_not_supported",
                    summary="Tier reset not supported",
                )

            result = await openclaw.reset_cloud_tier()
            return build_ops_response(status="ok", data={"result": result})
        except Exception as exc:  # noqa: BLE001
            return build_ops_response(
                status="failed", error_code="tier_reset_error", summary=str(exc)
            )

    # ---------- POST /api/openclaw/channels/runtime-repair (Wave N) ------
    @router.post("/api/openclaw/channels/runtime-repair")
    async def openclaw_runtime_repair(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Запуск скрипта восстановления рантайма OpenClaw.

        Требует WEB_API_KEY. Запускает ``openclaw_runtime_repair.command``
        из корня проекта с timeout 60s.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        script_path = str(ctx.project_root / "openclaw_runtime_repair.command")

        try:
            proc = await asyncio.create_subprocess_exec(
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            output = stdout.decode("utf-8", errors="replace")
            return {
                "ok": proc.returncode == 0,
                "output": output,
                "exit_code": proc.returncode,
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "timeout",
                "detail": "Скрипт выполнялся слишком долго (60с)",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "system_error", "detail": str(exc)}

    # ---------- POST /api/openclaw/channels/signal-guard-run (Wave N) ----
    @router.post("/api/openclaw/channels/signal-guard-run")
    async def openclaw_signal_guard_run(
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Однократный запуск Ops Guard для проверки сигналов.

        Требует WEB_API_KEY. Запускает ``scripts/signal_ops_guard.py --once``.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        script_path = "/Users/pablito/Antigravity_AGENTS/Краб/scripts/signal_ops_guard.py"

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                "--once",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            output = stdout.decode("utf-8", errors="replace")
            return {
                "ok": proc.returncode == 0,
                "output": output,
                "exit_code": proc.returncode,
            }
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "timeout",
                "detail": "Signal Guard выполнялся слишком долго (60с)",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "system_error", "detail": str(exc)}

    # ---------- GET /api/openclaw/cron/status (Wave DD) -------------------
    @router.get("/api/openclaw/cron/status")
    async def openclaw_cron_status() -> dict:
        """Truthful snapshot scheduler и recurring jobs из OpenClaw CLI."""
        helper = ctx.get_dep("openclaw_cron_snapshot_helper")
        if helper is None:
            return {"ok": False, "error": "helper_unavailable"}
        try:
            result = helper(include_all=True)
            if inspect.isawaitable(result):
                snapshot = await asyncio.wait_for(result, timeout=5.0)
            else:
                snapshot = result
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }
        if not snapshot.get("ok"):
            return snapshot
        return snapshot

    # ---------- GET /api/openclaw/cron/jobs (Wave DD) ---------------------
    @router.get("/api/openclaw/cron/jobs")
    async def openclaw_cron_jobs(include_all: bool = Query(default=True)) -> dict:
        """Recurring jobs для owner UI без дублирования cron-движка."""
        helper = ctx.get_dep("openclaw_cron_snapshot_helper")
        if helper is None:
            return {"ok": False, "error": "helper_unavailable"}
        try:
            result = helper(include_all=bool(include_all))
            if inspect.isawaitable(result):
                snapshot = await asyncio.wait_for(result, timeout=5.0)
            else:
                snapshot = result
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }
        if not snapshot.get("ok"):
            return snapshot
        return {
            "ok": True,
            "summary": snapshot.get("summary") or {},
            "jobs": snapshot.get("jobs") or [],
        }

    # ---------- GET /api/openclaw/runtime-config (Wave DD) ----------------
    @router.get("/api/openclaw/runtime-config")
    async def openclaw_runtime_config() -> dict:
        """Runtime-конфиг OpenClaw для UI (секрет masked, флаг присутствия)."""
        helper = ctx.get_dep("openclaw_runtime_config_snapshot_helper")
        if helper is None:
            return {"ok": False, "error": "helper_unavailable"}
        result = helper()
        if inspect.isawaitable(result):
            return await result
        return result

    # ---------- GET /api/openclaw/model-routing/status (Wave EE) ----------
    @router.get("/api/openclaw/model-routing/status")
    async def openclaw_model_routing_status() -> dict:
        """Read-only статус runtime model routing для owner-панели."""
        routing_helper = ctx.get_dep("openclaw_model_routing_helper")
        overlay_helper = ctx.get_dep("openclaw_model_routing_overlay_helper")
        if routing_helper is None or overlay_helper is None:
            return {"ok": False, "error": "helper_unavailable"}

        routing = routing_helper()
        if inspect.isawaitable(routing):
            routing = await routing

        last_runtime_route: dict = {}
        openclaw = ctx.get_dep("openclaw_client")
        if openclaw is not None and hasattr(openclaw, "get_last_runtime_route"):
            try:
                last_runtime_route = dict(openclaw.get_last_runtime_route() or {})
            except Exception:  # noqa: BLE001
                last_runtime_route = {}

        overlaid = overlay_helper(routing=routing, last_runtime_route=last_runtime_route)
        if inspect.isawaitable(overlaid):
            overlaid = await overlaid

        return {"ok": True, "routing": overlaid}

    # ---------- GET /api/openclaw/model-autoswitch/status (Wave EE) ------
    @router.get("/api/openclaw/model-autoswitch/status")
    async def openclaw_model_autoswitch_status(
        profile: str = Query(default="current"),
    ) -> dict:
        """Read-only статус autoswitch (dry-run) без изменения runtime-конфига."""
        helper = ctx.get_dep("openclaw_model_autoswitch_helper")
        if helper is None:
            raise HTTPException(status_code=500, detail="helper_unavailable")
        result = helper(dry_run=True, profile=profile, toggle=False)
        if inspect.isawaitable(result):
            result = await result
        return {"ok": True, "autoswitch": result}

    # ---------- GET /api/openclaw/model-compat/probe (Wave EE) ------------
    @router.get("/api/openclaw/model-compat/probe")
    async def openclaw_model_compat_probe(
        model: str = Query(default=""),
        reasoning: str = Query(default="high"),
        skip_reasoning: bool = Query(default=False),
    ) -> dict:
        """Read-only compatibility probe для target-модели через OpenClaw gateway."""
        helper = ctx.get_dep("openclaw_model_compat_probe_helper")
        if helper is None:
            raise HTTPException(status_code=500, detail="helper_unavailable")
        result = helper(
            model=model,
            reasoning=reasoning,
            skip_reasoning=skip_reasoning,
        )
        if inspect.isawaitable(result):
            result = await result
        return {"ok": True, "probe": result}

    return router
