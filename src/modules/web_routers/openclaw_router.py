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

Endpoints (Wave JJ, POST через CLI helper injection):
- POST /api/openclaw/cron/jobs/create   — `openclaw_cli_runner_helper` + cron snapshot
- POST /api/openclaw/cron/jobs/toggle   — same helpers
- POST /api/openclaw/cron/jobs/remove   — same helpers
- POST /api/openclaw/model-autoswitch/apply — через `openclaw_model_autoswitch_helper`

Endpoints (Wave KK, GET self-contained):
- GET /api/openclaw/cloud               — `openclaw.get_cloud_provider_diagnostics`
- GET /api/openclaw/cloud/diagnostics   — legacy alias (тот же impl)
- GET /api/openclaw/control-compat/status — subprocess (`openclaw channels status --probe`,
                                            `openclaw logs --tail 200`), pure self-contained

Endpoints (Wave LL, browser/smoke через helper injection):
- GET  /api/openclaw/browser-smoke              — `openclaw_browser_smoke_helper`
- GET  /api/openclaw/photo-smoke                — `openclaw_photo_smoke_helper`
- POST /api/openclaw/browser/open-owner-chrome  — `openclaw_launch_owner_chrome_helper`

Endpoints (Wave MM, browser readiness/start через helper injection):
- GET  /api/openclaw/browser-mcp-readiness — `openclaw_browser_smoke_helper`
                                              + `openclaw_probe_owner_chrome_helper`
                                              + `openclaw_collect_stable_browser_cli_runtime_helper`
                                              + `openclaw_classify_browser_stage_helper`
                                              + `openclaw_build_mcp_readiness_snapshot_helper`
                                              + `openclaw_build_browser_access_paths_helper`
- POST /api/openclaw/browser/start         — `openclaw_run_cli_json_helper`
                                              + browser smoke/probe/runtime/classify helpers

Endpoints (Wave NN, оставшиеся HARD через helper injection):
- GET  /api/openclaw/channels/status   — `openclaw_cli_env_helper` +
                                          `openclaw_parse_channels_probe_helper`
- GET  /api/openclaw/cloud/runtime-check — `runtime_lite_cache_invalidator_helper`
                                            + `openclaw_client.get_cloud_runtime_check`
- POST /api/openclaw/cloud/switch-tier  — `runtime_lite_cache_invalidator_helper`
                                           + `openclaw_client.switch_cloud_tier`
- GET  /api/openclaw/routing/effective  — `resolve_local_runtime_truth_helper`
                                           + ``router`` dep + inline force_mode normalize

SKIP (HARD, требуют дополнительный helper promote):
- /api/openclaw/cron/jobs/run_now      — cron_native_scheduler internals (W32 debug)

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.
"""

from __future__ import annotations

import asyncio
import inspect
import sys

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from src.core.observability import build_ops_response

from ._context import RouterContext

# /api/openclaw/control-compat/status (Wave KK): источники маркеров schema-warnings.
# Хранятся как module-level set, чтобы тесты могли при необходимости расширять
# или подменять через monkeypatch без изменения сигнатуры функции.
_CONTROL_COMPAT_SCHEMA_MARKERS = frozenset({"unsupported schema node", "schema", "validation"})


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

    # ---------- Wave JJ helpers ------------------------------------------
    async def _resolve_cli(*args, **kwargs):
        helper = ctx.get_dep("openclaw_cli_runner_helper")
        if helper is None:
            return None
        result = helper(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _resolve_cron_snapshot(*, include_all: bool = True):
        helper = ctx.get_dep("openclaw_cron_snapshot_helper")
        if helper is None:
            return None
        result = helper(include_all=include_all)
        if inspect.isawaitable(result):
            result = await result
        return result

    # ---------- POST /api/openclaw/cron/jobs/create (Wave JJ) ------------
    @router.post("/api/openclaw/cron/jobs/create")
    async def openclaw_cron_job_create(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создаёт recurring cron job через нативный `openclaw cron add`."""
        ctx.assert_write_access(x_krab_web_key, token)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="cron_create_body_required")

        name = str(body.get("name") or "").strip()
        every = str(body.get("every") or "").strip()
        task_kind = str(body.get("task_kind") or "system").strip().lower()
        payload_text = str(body.get("payload_text") or "").strip()
        session_target = str(body.get("session_target") or "main").strip().lower()
        wake_mode = str(body.get("wake_mode") or "now").strip().lower()
        agent_id = str(body.get("agent_id") or "main").strip()
        thinking = str(body.get("thinking") or "").strip().lower()
        model = str(body.get("model") or "").strip()
        description = str(body.get("description") or "").strip()

        if not name:
            raise HTTPException(status_code=400, detail="cron_name_required")
        if not every:
            raise HTTPException(status_code=400, detail="cron_every_required")
        if not payload_text:
            raise HTTPException(status_code=400, detail="cron_payload_required")
        if task_kind not in {"system", "agent"}:
            raise HTTPException(status_code=400, detail="cron_task_kind_invalid")
        if session_target not in {"main", "isolated"}:
            raise HTTPException(status_code=400, detail="cron_session_target_invalid")
        if wake_mode not in {"now", "next-heartbeat"}:
            raise HTTPException(status_code=400, detail="cron_wake_mode_invalid")

        command: list[str] = [
            "cron",
            "add",
            "--json",
            "--name",
            name,
            "--every",
            every,
            "--session",
            session_target,
            "--wake",
            wake_mode,
        ]
        if description:
            command.extend(["--description", description])
        if bool(body.get("disabled")):
            command.append("--disabled")
        if bool(body.get("announce")):
            command.append("--announce")
        if task_kind == "agent":
            command.extend(["--agent", agent_id or "main", "--message", payload_text])
            if thinking:
                command.extend(["--thinking", thinking])
            if model:
                command.extend(["--model", model])
        else:
            command.extend(["--system-event", payload_text])

        create_result = await _resolve_cli(*command, timeout=45.0, expect_json=True)
        if create_result is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not create_result.get("ok"):
            return {
                "ok": False,
                "error": create_result.get("error") or "cron_create_failed",
                "detail": create_result.get("detail")
                or create_result.get("raw")
                or "Не удалось создать recurring job",
            }

        snapshot = await _resolve_cron_snapshot(include_all=True)
        if snapshot is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not snapshot.get("ok"):
            return snapshot
        return {
            "ok": True,
            "created": create_result.get("data") or {},
            "summary": snapshot.get("summary") or {},
            "jobs": snapshot.get("jobs") or [],
            "status": snapshot.get("status") or {},
        }

    # ---------- POST /api/openclaw/cron/jobs/toggle (Wave JJ) ------------
    @router.post("/api/openclaw/cron/jobs/toggle")
    async def openclaw_cron_job_toggle(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Включает или выключает recurring job через OpenClaw CLI."""
        ctx.assert_write_access(x_krab_web_key, token)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="cron_toggle_body_required")
        job_id = str(body.get("id") or "").strip()
        enabled = body.get("enabled")
        if not job_id:
            raise HTTPException(status_code=400, detail="cron_id_required")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="cron_enabled_bool_required")

        command = ["cron", "enable" if enabled else "disable", job_id]
        toggle_result = await _resolve_cli(*command, timeout=35.0, expect_json=False)
        if toggle_result is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not toggle_result.get("ok"):
            return {
                "ok": False,
                "error": toggle_result.get("error") or "cron_toggle_failed",
                "detail": toggle_result.get("detail")
                or toggle_result.get("raw")
                or "Не удалось изменить состояние recurring job",
            }

        snapshot = await _resolve_cron_snapshot(include_all=True)
        if snapshot is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not snapshot.get("ok"):
            return snapshot
        return {
            "ok": True,
            "detail": toggle_result.get("raw") or "",
            "summary": snapshot.get("summary") or {},
            "jobs": snapshot.get("jobs") or [],
            "status": snapshot.get("status") or {},
        }

    # ---------- POST /api/openclaw/cron/jobs/remove (Wave JJ) ------------
    @router.post("/api/openclaw/cron/jobs/remove")
    async def openclaw_cron_job_remove(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Удаляет recurring job через OpenClaw CLI."""
        ctx.assert_write_access(x_krab_web_key, token)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="cron_remove_body_required")
        job_id = str(body.get("id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="cron_id_required")

        remove_result = await _resolve_cli(
            "cron", "rm", "--json", job_id, timeout=35.0, expect_json=True
        )
        if remove_result is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not remove_result.get("ok"):
            return {
                "ok": False,
                "error": remove_result.get("error") or "cron_remove_failed",
                "detail": remove_result.get("detail")
                or remove_result.get("raw")
                or "Не удалось удалить recurring job",
            }

        snapshot = await _resolve_cron_snapshot(include_all=True)
        if snapshot is None:
            return {"ok": False, "error": "helper_unavailable"}
        if not snapshot.get("ok"):
            return snapshot
        return {
            "ok": True,
            "removed": remove_result.get("data") or {},
            "summary": snapshot.get("summary") or {},
            "jobs": snapshot.get("jobs") or [],
            "status": snapshot.get("status") or {},
        }

    # ---------- POST /api/openclaw/model-autoswitch/apply (Wave JJ) -------
    @router.post("/api/openclaw/model-autoswitch/apply")
    async def openclaw_model_autoswitch_apply(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
        profile: str = Query(default=""),
    ) -> dict:
        """Применяет autoswitch runtime-конфига OpenClaw (write endpoint)."""
        ctx.assert_write_access(x_krab_web_key, token)
        helper = ctx.get_dep("openclaw_model_autoswitch_helper")
        if helper is None:
            raise HTTPException(status_code=500, detail="helper_unavailable")

        body: dict = {}
        try:
            body_raw = await request.json()
            if isinstance(body_raw, dict):
                body = body_raw
        except Exception:  # noqa: BLE001
            body = {}

        body_profile = str(body.get("profile") or "").strip()
        body_toggle_raw = body.get("toggle")
        body_toggle = False
        if isinstance(body_toggle_raw, bool):
            body_toggle = body_toggle_raw
        elif body_toggle_raw is not None:
            body_toggle = str(body_toggle_raw).strip().lower() in {"1", "true", "yes", "on"}

        effective_profile = body_profile or profile
        effective_toggle = body_toggle or (not effective_profile)
        result = helper(
            dry_run=False,
            profile=effective_profile,
            toggle=effective_toggle,
        )
        if inspect.isawaitable(result):
            result = await result
        return {"ok": True, "autoswitch": result}

    # ---------- Wave KK: cloud diagnostics impl --------------------------
    async def _cloud_diagnostics_impl(providers: str = "") -> dict:
        """Проверка cloud-провайдеров OpenClaw с классификацией ошибок ключей/API.

        Зеркалирует ``WebApp._openclaw_cloud_diagnostics_impl`` (web_app.py).
        """
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_cloud_provider_diagnostics"):
            return {"available": False, "error": "cloud_diagnostics_not_supported"}

        providers_list: list[str] | None = None
        raw = (providers or "").strip()
        if raw:
            providers_list = [item.strip().lower() for item in raw.split(",") if item.strip()]
            if not providers_list:
                providers_list = None
        report = await openclaw.get_cloud_provider_diagnostics(providers=providers_list)
        return {"available": True, "report": report}

    # ---------- GET /api/openclaw/cloud (Wave KK) -------------------------
    @router.get("/api/openclaw/cloud")
    async def openclaw_cloud_diagnostics(providers: str = Query(default="")) -> dict:
        """Канонический endpoint cloud-диагностики."""
        return await _cloud_diagnostics_impl(providers=providers)

    # ---------- GET /api/openclaw/cloud/diagnostics (Wave KK) ------------
    @router.get("/api/openclaw/cloud/diagnostics")
    async def openclaw_cloud_diagnostics_legacy(providers: str = Query(default="")) -> dict:
        """Совместимость со старым UI-клиентом (legacy alias)."""
        return await _cloud_diagnostics_impl(providers=providers)

    # ---------- GET /api/openclaw/control-compat/status (Wave KK) --------
    @router.get("/api/openclaw/control-compat/status")
    async def openclaw_control_compat_status() -> dict:
        """[R22] Control Compatibility Diagnostics.

        Дает прозрачный ответ на вопрос: предупреждения OpenClaw Control UI
        (`Unsupported schema node`) — это UI-артефакт или реальный runtime-риск?

        Источники:
        - `openclaw channels status --probe` → runtime_channels_ok
        - `openclaw logs --tail 200` → control_schema_warnings (фильтрация по маркерам)

        Логика impact_level:
        - runtime ok + warnings → "ui_only"
        - runtime fail + warnings → "runtime_risk"
        - runtime ok, warnings нет → "none"
        """

        async def _inner() -> dict:
            # --- Шаг 1: проверяем runtime каналов ---
            runtime_ok = False
            try:
                proc_channels = await asyncio.create_subprocess_exec(
                    "openclaw",
                    "channels",
                    "status",
                    "--probe",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    await asyncio.wait_for(proc_channels.communicate(), timeout=30.0)
                    runtime_ok = proc_channels.returncode == 0
                except asyncio.TimeoutError:
                    try:
                        proc_channels.terminate()
                    except ProcessLookupError:
                        pass
                    runtime_ok = False
            except Exception:  # noqa: BLE001
                runtime_ok = False

            # --- Шаг 2: получаем последние логи OpenClaw для поиска schema-маркеров ---
            control_schema_warnings: list[str] = []
            try:
                proc_logs = await asyncio.create_subprocess_exec(
                    "openclaw",
                    "logs",
                    "--tail",
                    "200",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_logs, _ = await asyncio.wait_for(proc_logs.communicate(), timeout=10.0)
                    raw_logs = stdout_logs.decode("utf-8", errors="replace")
                    for line in raw_logs.splitlines():
                        line_lower = line.lower()
                        if any(marker in line_lower for marker in _CONTROL_COMPAT_SCHEMA_MARKERS):
                            stripped = line.strip()
                            if stripped:
                                control_schema_warnings.append(stripped)
                except asyncio.TimeoutError:
                    try:
                        proc_logs.terminate()
                    except ProcessLookupError:
                        pass
            except Exception:  # noqa: BLE001
                pass

            # --- Шаг 3: определяем impact_level и рекомендацию ---
            has_warnings = bool(control_schema_warnings)
            if runtime_ok and has_warnings:
                impact_level = "ui_only"
                recommended_action = (
                    "Предупреждения ограничены UI Control. Runtime каналов работает нормально. "
                    "Для редактирования затронутых полей используй Raw-режим в Control Dashboard."
                )
            elif not runtime_ok and has_warnings:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Обнаружены schema-предупреждения И проблемы runtime. "
                    "Запусти: openclaw doctor --fix  или  ./openclaw_runtime_repair.command"
                )
            elif not runtime_ok:
                impact_level = "runtime_risk"
                recommended_action = (
                    "Runtime каналов недоступен. Schema-предупреждения не обнаружены. "
                    "Запусти: openclaw doctor --fix"
                )
            else:
                impact_level = "none"
                recommended_action = "Все каналы работают нормально. Предупреждений нет."

            return {
                "ok": runtime_ok or not has_warnings,
                "runtime_channels_ok": runtime_ok,
                "runtime_status": "OK" if runtime_ok else "FAIL",
                "control_schema_warnings": control_schema_warnings,
                "has_schema_warning": has_warnings,
                "impact_level": impact_level,
                "recommended_action": recommended_action,
            }

        # Верхний guard: не зависаем если CLI не стартует.
        try:
            return await asyncio.wait_for(_inner(), timeout=5.0)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }

    # ---------- Wave LL: browser/photo smoke endpoints --------------------

    # ---------- GET /api/openclaw/browser-smoke (Wave LL) ----------------
    @router.get("/api/openclaw/browser-smoke")
    async def openclaw_browser_smoke(url: str = "https://example.com") -> dict:
        """Browser relay smoke check с явным attached/not attached статусом.

        Контур:
        1) `openclaw gateway probe` (reachability gateway ws),
        2) HTTP probe browser-server (`http://127.0.0.1:18791/`).
        """
        helper = ctx.get_dep("openclaw_browser_smoke_helper")
        if helper is None:
            return {
                "available": False,
                "error": "openclaw_browser_smoke_helper_unavailable",
            }
        # Верхний guard: не зависаем если gateway не отвечает.
        try:
            coro = helper(url)
            if inspect.isawaitable(coro):
                report = await asyncio.wait_for(coro, timeout=5.0)
            else:
                report = coro
        except asyncio.TimeoutError:
            return {
                "available": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }
        return {
            "available": True,
            "report": report,
        }

    # ---------- GET /api/openclaw/photo-smoke (Wave LL) ------------------
    @router.get("/api/openclaw/photo-smoke")
    async def openclaw_photo_smoke() -> dict:
        """Легковесная проверка готовности photo/vision маршрута.

        Проверяет:
        1) доступ к model manager через router;
        2) наличие vision-capable локальных моделей;
        3) выбранную модель для `has_photo=True`.
        """
        helper = ctx.get_dep("openclaw_photo_smoke_helper")
        if helper is None:
            return {
                "available": False,
                "error": "openclaw_photo_smoke_helper_unavailable",
            }
        result = helper()
        if inspect.isawaitable(result):
            result = await result
        return result

    # ---------- POST /api/openclaw/browser/open-owner-chrome (Wave LL) ---
    @router.post("/api/openclaw/browser/open-owner-chrome")
    async def openclaw_browser_open_owner_chrome(
        token: str = Query(default=""),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
    ) -> dict:
        """Открывает helper для relaunch обычного Chrome владельца с Remote Debugging."""
        ctx.assert_write_access(x_krab_web_key, token)
        helper = ctx.get_dep("openclaw_launch_owner_chrome_helper")
        if helper is None:
            raise HTTPException(status_code=500, detail="helper_unavailable")
        result = helper()
        if inspect.isawaitable(result):
            result = await result
        return result

    # ---------- Wave MM: browser readiness/start endpoints ---------------

    async def _maybe_await(value):
        """Helper: await если awaitable, иначе вернуть как есть."""
        if inspect.isawaitable(value):
            return await value
        return value

    async def _gather_browser_state(url: str, *, write_pre_check: bool = False):
        """Общий контур сбора browser readiness state.

        Возвращает tuple: (smoke_report, owner_chrome, browser_status,
        browser_status_error, tabs_payload, tabs_error, browser, smoke).
        """
        smoke_helper = ctx.get_dep("openclaw_browser_smoke_helper")
        probe_helper = ctx.get_dep("openclaw_probe_owner_chrome_helper")
        runtime_helper = ctx.get_dep("openclaw_collect_stable_browser_cli_runtime_helper")
        classify_helper = ctx.get_dep("openclaw_classify_browser_stage_helper")

        # smoke + owner_chrome параллельно
        smoke_task = _maybe_await(smoke_helper(url)) if smoke_helper else None
        owner_task = _maybe_await(probe_helper(url)) if probe_helper else None
        if smoke_task is not None and owner_task is not None:
            smoke_report, owner_chrome = await asyncio.gather(smoke_task, owner_task)
        else:
            smoke_report = await smoke_task if smoke_task is not None else {}
            owner_chrome = await owner_task if owner_task is not None else {}

        smoke = dict((smoke_report or {}).get("browser_smoke", {}) or {})

        if runtime_helper is None:
            return (smoke_report, owner_chrome, {}, None, {}, None, {}, smoke)

        runtime_result = runtime_helper(
            relay_reachable=bool(
                smoke.get("relay_reachable") or smoke.get("browser_http_reachable")
            ),
            auth_required=bool(smoke.get("browser_auth_required")),
            attempts=3,
            settle_delay_sec=0.8,
        )
        runtime_result = await _maybe_await(runtime_result)
        browser_status, browser_status_error, tabs_payload, tabs_error = runtime_result

        if classify_helper is None:
            browser = {}
        else:
            browser = classify_helper(
                browser_status,
                tabs_payload,
                smoke,
                browser_status_error=browser_status_error,
                tabs_error=tabs_error,
            )
            browser = await _maybe_await(browser)
        return (
            smoke_report,
            owner_chrome,
            browser_status,
            browser_status_error,
            tabs_payload,
            tabs_error,
            browser,
            smoke,
        )

    # ---------- GET /api/openclaw/browser-mcp-readiness (Wave MM) --------
    @router.get("/api/openclaw/browser-mcp-readiness")
    async def openclaw_browser_mcp_readiness(url: str = "https://example.com") -> dict:
        """Агрегированный staged readiness для browser-контура владельца и managed MCP."""

        async def _run_readiness() -> dict:
            (
                smoke_report,
                owner_chrome,
                browser_status,
                browser_status_error,
                tabs_payload,
                tabs_error,
                browser,
                smoke,
            ) = await _gather_browser_state(url)

            mcp_helper = ctx.get_dep("openclaw_build_mcp_readiness_snapshot_helper")
            paths_helper = ctx.get_dep("openclaw_build_browser_access_paths_helper")
            mcp = (
                await _maybe_await(mcp_helper(browser, owner_chrome=owner_chrome))
                if mcp_helper
                else {}
            )
            if paths_helper is not None and isinstance(browser, dict):
                browser["paths"] = await _maybe_await(paths_helper(browser, mcp))

            overall = "ready"
            if "blocked" in {
                str((browser or {}).get("readiness")),
                str((mcp or {}).get("readiness")),
            }:
                overall = "blocked"
            elif "attention" in {
                str((browser or {}).get("readiness")),
                str((mcp or {}).get("readiness")),
            }:
                overall = "attention"

            return {
                "available": True,
                "overall": {
                    "readiness": overall,
                    "detail": (
                        "Browser relay и managed MCP готовы."
                        if overall == "ready"
                        else "Есть оставшиеся шаги для browser/MCP readiness."
                    ),
                },
                "browser": browser,
                "mcp": mcp,
                "raw": {
                    "browser_status": browser_status,
                    "browser_status_error": browser_status_error,
                    "tabs": tabs_payload,
                    "tabs_error": tabs_error,
                    "browser_smoke": smoke_report,
                    "owner_chrome": owner_chrome,
                },
            }

        try:
            return await asyncio.wait_for(_run_readiness(), timeout=5.0)
        except asyncio.TimeoutError:
            return {
                "available": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }

    # ---------- POST /api/openclaw/browser/start (Wave MM) ---------------
    @router.post("/api/openclaw/browser/start")
    async def openclaw_browser_start(
        token: str = Query(default=""),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
    ) -> dict:
        """Явно поднимает dedicated OpenClaw browser и возвращает обновлённый readiness snapshot."""
        ctx.assert_write_access(x_krab_web_key, token)

        cli_helper = ctx.get_dep("openclaw_run_cli_json_helper")
        if cli_helper is None:
            raise HTTPException(status_code=500, detail="cli_helper_unavailable")

        cli_result = cli_helper(["browser", "--json", "start"], timeout_sec=20.0)
        cli_result = await _maybe_await(cli_result)
        start_payload, start_error = cli_result
        if start_error:
            return {
                "ok": False,
                "error": "browser_start_failed",
                "detail": start_error,
            }

        (
            smoke_report,
            owner_chrome,
            browser_status,
            browser_status_error,
            tabs_payload,
            tabs_error,
            browser,
            _smoke,
        ) = await _gather_browser_state("https://example.com")

        return {
            "ok": True,
            "start": start_payload,
            "browser": browser,
            "raw": {
                "browser_status": browser_status,
                "browser_status_error": browser_status_error,
                "tabs": tabs_payload,
                "tabs_error": tabs_error,
                "browser_smoke": smoke_report,
                "owner_chrome": owner_chrome,
            },
        }

    # =====================================================================
    # Wave NN — оставшиеся HARD endpoints через helper injection
    # =====================================================================

    def _normalize_force_mode_local(force_mode: str) -> str:
        """Нормализует внутренние force_* режимы в UI-вид: auto/local/cloud.

        Локальная копия логики из ``WebApp._setup_routes::_normalize_force_mode``
        (Wave NN). Pure-функция, поэтому inline-копирование безопаснее, чем
        helper-injection.
        """
        normalized = str(force_mode or "").strip().lower()
        if normalized in {"force_local", "local"}:
            return "local"
        if normalized in {"force_cloud", "cloud"}:
            return "cloud"
        return "auto"

    # ---------- GET /api/openclaw/channels/status (Wave NN) --------------
    @router.get("/api/openclaw/channels/status")
    async def openclaw_channels_status() -> dict:
        """Выполняет ``openclaw channels status --probe`` и возвращает сырой
        вывод + распарсенные предупреждения.
        """
        cli_env_helper = ctx.get_dep("openclaw_cli_env_helper")
        parse_helper = ctx.get_dep("openclaw_parse_channels_probe_helper")
        if cli_env_helper is None or parse_helper is None:
            return {
                "ok": False,
                "error": "system_error",
                "detail": "channels_status_helpers_unavailable",
            }

        async def _inner() -> dict:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "channels",
                "status",
                "--probe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=cli_env_helper(),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
            except asyncio.TimeoutError:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                return {
                    "ok": False,
                    "error": "openclaw_timeout",
                    "detail": "Запрос статуса каналов превысил 45 сек.",
                }

            raw_output = stdout.decode("utf-8", errors="replace")

            parsed = parse_helper(raw_output)
            warnings = list(parsed.get("warnings") or [])
            if not warnings:
                for line in raw_output.splitlines():
                    if "WARN" in line.upper():
                        warnings.append(line.strip())

            return {
                "ok": proc.returncode == 0,
                "raw": raw_output,
                "warnings": warnings,
                "exit_code": proc.returncode,
                "channels": parsed.get("channels") or [],
                "gateway_reachable": bool(parsed.get("gateway_reachable")),
            }

        try:
            return await asyncio.wait_for(_inner(), timeout=5.0)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "OpenClaw timeout (5s)",
                "detail": "gateway not responding",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": "system_error",
                "detail": f"Не удалось выполнить openclaw: {exc}",
            }

    # ---------- GET /api/openclaw/cloud/runtime-check (Wave NN) ----------
    @router.get("/api/openclaw/cloud/runtime-check")
    async def openclaw_cloud_runtime_check() -> dict:
        """Runtime-check cloud key chain (masked).

        После truthful runtime-check tier-state может измениться (stale ``free``
        → реальный ``paid``), поэтому lightweight runtime snapshot нужно
        пересобрать заново — invalidate cache через injected helper.
        """
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"available": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "get_cloud_runtime_check"):
            return {"available": False, "error": "cloud_runtime_check_not_supported"}
        try:
            report = await openclaw.get_cloud_runtime_check()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "error": "cloud_runtime_check_failed",
                "detail": str(exc),
            }
        invalidator = ctx.get_dep("runtime_lite_cache_invalidator_helper")
        if invalidator is not None:
            try:
                invalidator()
            except Exception:  # noqa: BLE001
                pass
        return {"available": True, "report": report}

    # ---------- POST /api/openclaw/cloud/switch-tier (Wave NN) -----------
    @router.post("/api/openclaw/cloud/switch-tier")
    async def openclaw_cloud_switch_tier(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Ручное переключение cloud-tier (free/paid) + secrets reload."""
        ctx.assert_write_access(x_krab_web_key, token)
        openclaw = ctx.get_dep("openclaw_client")
        if not openclaw:
            return {"ok": False, "error": "openclaw_client_not_configured"}
        if not hasattr(openclaw, "switch_cloud_tier"):
            return {"ok": False, "error": "switch_cloud_tier_not_supported"}

        tier = str((payload or {}).get("tier", "free")).strip().lower()
        if tier not in {"free", "paid"}:
            return {"ok": False, "error": "invalid_tier", "detail": "Допустимо: free|paid"}
        try:
            result = await openclaw.switch_cloud_tier(tier)
            invalidator = ctx.get_dep("runtime_lite_cache_invalidator_helper")
            if invalidator is not None:
                try:
                    invalidator()
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": bool((result or {}).get("ok")), "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "switch_cloud_tier_failed", "detail": str(exc)}

    # ---------- GET /api/openclaw/routing/effective (Wave NN) ------------
    @router.get("/api/openclaw/routing/effective")
    async def openclaw_routing_effective() -> dict:
        """[R22] Routing Effective Source of Truth.

        Единый источник истины о текущем routing-решении Krab. Читает только
        существующие атрибуты роутера + резолвит local runtime truth через
        injected helper.
        """
        router_obj = ctx.get_dep("router")
        if router_obj is None:
            return {
                "ok": False,
                "error": "router_unavailable",
                "decision_notes": [],
            }

        force_mode_raw = str(getattr(router_obj, "force_mode", "auto") or "auto")
        force_mode_eff = _normalize_force_mode_local(force_mode_raw)

        cloud_slots: dict = {}
        raw_models = getattr(router_obj, "models", {}) or {}
        if isinstance(raw_models, dict):
            cloud_slots = {str(k): str(v) for k, v in raw_models.items()}
        default_slot = "chat" if "chat" in cloud_slots else (next(iter(cloud_slots), None) or "")
        default_model = cloud_slots.get(default_slot, "")

        cloud_fallback_enabled = force_mode_eff != "local"
        last_route: dict = {}
        try:
            getter = getattr(router_obj, "get_last_route", None)
            if callable(getter):
                candidate = getter() or {}
                if isinstance(candidate, dict):
                    last_route = candidate
        except Exception:  # noqa: BLE001
            last_route = {}

        truth_helper = ctx.get_dep("resolve_local_runtime_truth_helper")
        if truth_helper is None:
            local_truth: dict = {}
        else:
            try:
                local_truth = await truth_helper(router_obj)
                if not isinstance(local_truth, dict):
                    local_truth = {}
            except Exception:  # noqa: BLE001
                local_truth = {}

        local_engine = str(
            local_truth.get("engine") or getattr(router_obj, "local_engine", "") or ""
        )
        local_available = bool(local_truth.get("runtime_reachable"))
        active_local_model = str(local_truth.get("active_model") or "")
        routing_policy = str(
            getattr(router_obj, "routing_policy", "free_first_hybrid") or "free_first_hybrid"
        )
        cloud_cap_reached = bool(getattr(router_obj, "cloud_soft_cap_reached", False))
        last_route_status = str(last_route.get("status") or "").strip().lower()
        last_route_channel = str(last_route.get("channel") or "").strip().lower()
        last_route_model = str(last_route.get("model") or "").strip()

        current_route_uses_cloud = bool(
            last_route_status == "ok" and last_route_channel in {"openclaw_cloud", "cloud"}
        )
        current_fallback_active = False
        if force_mode_eff == "cloud" and cloud_fallback_enabled:
            current_fallback_active = True
        elif not cloud_fallback_enabled:
            current_fallback_active = False
        elif last_route_status == "ok":
            if (
                current_route_uses_cloud
                and default_model
                and last_route_model
                and last_route_model != default_model
            ):
                current_fallback_active = True
            elif (
                not current_route_uses_cloud
                and last_route_model
                and active_local_model
                and last_route_model != active_local_model
            ):
                current_fallback_active = True
        elif not local_available and force_mode_eff not in {"cloud"} and cloud_fallback_enabled:
            current_fallback_active = True

        if not cloud_fallback_enabled:
            cloud_fallback_state = "disabled"
        elif current_fallback_active:
            cloud_fallback_state = "active"
        else:
            cloud_fallback_state = "standby"

        decision_notes: list[str] = []
        if force_mode_raw in {"force_local", "local"}:
            decision_notes.append(
                f"Принудительный local-режим активен — все запросы идут через {local_engine or 'local'}."
            )
        elif force_mode_raw in {"force_cloud", "cloud"}:
            decision_notes.append(
                "Принудительный cloud-режим активен — локальный движок пропускается."
            )
        else:
            decision_notes.append(f"Routing policy: {routing_policy} — auto-routing включен.")

        if local_available:
            decision_notes.append(
                f"Локальный движок '{local_engine}' доступен."
                + (f" Активная модель: '{active_local_model}'." if active_local_model else "")
            )
        else:
            decision_notes.append("Локальный движок недоступен — fallback только на cloud.")

        if cloud_cap_reached:
            decision_notes.append(
                "Cloud soft-cap достигнут: приоритет переключен на локальный движок."
            )

        if not cloud_fallback_enabled:
            decision_notes.append(
                "Cloud fallback ОТКЛЮЧЕН: force_local режим запрещает обращение к cloud."
            )
        elif cloud_fallback_state == "active":
            decision_notes.append("Cloud fallback сейчас задействован как активный маршрут.")
        else:
            decision_notes.append("Cloud fallback доступен как резерв, но сейчас не задействован.")

        active_slot_or_model = (
            last_route_model or active_local_model or default_model or default_slot
        )

        return {
            "ok": True,
            "requested_mode": force_mode_raw,
            "effective_mode": force_mode_eff,
            "active_slot_or_model": active_slot_or_model,
            "cloud_fallback": cloud_fallback_enabled,
            "cloud_fallback_state": cloud_fallback_state,
            "cloud_fallback_active": current_fallback_active,
            "cloud_route_active": current_route_uses_cloud,
            "force_mode_requested": force_mode_raw,
            "force_mode_effective": force_mode_eff,
            "assistant_default_slot": default_slot,
            "assistant_default_model": default_model,
            "cloud_fallback_enabled": cloud_fallback_enabled,
            "decision_notes": decision_notes,
        }

    return router
