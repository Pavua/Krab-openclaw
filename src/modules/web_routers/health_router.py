# -*- coding: utf-8 -*-
"""
Health router — Phase 2 Wave X extraction (Session 25).

Объединяет health и ecosystem read-only endpoints:
- GET /api/health                 — единый health для web-панели (uses EcosystemHealthService)
- GET /api/health/lite            — fast liveness (runtime_lite snapshot)
- GET /api/health/deep            — расширенная диагностика (12 секций, Session 24)
- GET /api/v1/health              — versioned health для внешних мониторов
- GET /api/ecosystem/health       — расширенный health 3-проектной экосистемы
- GET /api/ecosystem/health/debug — raw collector output для diagnose
- GET /api/ecosystem/health/export — экспорт ecosystem health в JSON file
- GET /api/network/probes         — Wave 163: split-brain detection state +
                                    pyrogram метрики (восстановлено после Session 47)

Wave CC (Session 25): /api/health/deep extracted. Existing tests
(``test_api_health_deep.py``, ``test_health_deep_session24.py``,
``test_health_deep_orphans.py``) патчат
``src.core.health_deep_collector.collect_health_deep`` напрямую,
поэтому extraction safe — функция импортируется внутри handler'а.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Все endpoints — read-only (GET без write_access checks).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...core.logger import get_logger
from ._context import RouterContext

logger = get_logger(__name__)


def build_health_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с health + ecosystem endpoints."""
    router = APIRouter(tags=["health"])

    # ── /api/health ─────────────────────────────────────────────────────────

    @router.get("/api/health")
    async def get_health() -> dict:
        """Единый health статусов для web-панели."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.deps["router"]
        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")
        lite_snapshot = await ctx.collect_runtime_lite()
        lm_state = str(lite_snapshot.get("lmstudio_model_state") or "unknown").strip().lower()
        local_ok = lm_state in {"loaded", "idle"}
        ecosystem = EcosystemHealthService(
            router=router_dep,
            openclaw_client=openclaw,
            voice_gateway_client=voice_gateway,
            krab_ear_client=krab_ear,
            local_health_override={
                "ok": local_ok,
                "status": "ok" if local_ok else (lm_state or "down"),
                "degraded": not local_ok,
                "latency_ms": 0,
                "source": "web_app.lite_snapshot",
            },
        )
        report = await ecosystem.collect()
        return {
            "status": "ok",
            "checks": {
                "openclaw": bool(report["checks"]["openclaw"]["ok"]),
                "local_lm": local_ok,
                "voice_gateway": bool(report["checks"]["voice_gateway"]["ok"]),
                "krab_ear": bool(report["checks"]["krab_ear"]["ok"]),
            },
            "degradation": str(report["degradation"]),
            "risk_level": str(report["risk_level"]),
            "chain": report["chain"],
        }

    # ── /api/health/lite ────────────────────────────────────────────────────

    @router.get("/api/health/lite")
    async def get_health_lite() -> dict:
        """
        Быстрый liveness-check web-панели.

        Важно:
        - не тянет deep ecosystem probes;
        - используется daemon-скриптами и uptime-watch для проверки
          «жив ли HTTP-процесс», а не «все ли внешние зависимости сейчас быстрые».
        """
        # Импорт через web_app namespace — чтобы существующие тесты,
        # патчащие `_resolve_memory_indexer_state` через WebApp module,
        # продолжали работать. Dual-patch стратегия (Wave W).
        from .. import web_app as _wam

        runtime = await ctx.collect_runtime_lite()
        # B.7 (session 4): telegram_rate_limiter stats для /stats dashboard.
        try:
            from ...core.telegram_rate_limiter import telegram_rate_limiter as _trl

            _rate_limiter_stats = _trl.stats()
        except Exception:
            _rate_limiter_stats = None
        result = {
            "ok": True,
            "status": "up",
            "telegram_session_state": runtime.get("telegram_session_state"),
            "telegram_userbot_state": (
                (runtime.get("telegram_userbot") or {}).get("startup_state")
            ),
            "telegram_userbot_client_connected": (
                (runtime.get("telegram_userbot") or {}).get("client_connected")
            ),
            "telegram_userbot_error_code": (
                (runtime.get("telegram_userbot") or {}).get("startup_error_code")
            ),
            "lmstudio_model_state": runtime.get("lmstudio_model_state"),
            "openclaw_auth_state": runtime.get("openclaw_auth_state"),
            "last_runtime_route": runtime.get("last_runtime_route"),
            "scheduler_enabled": runtime.get("scheduler_enabled"),
            "inbox_summary": runtime.get("inbox_summary"),
            "voice_gateway_configured": runtime.get("voice_gateway_configured"),
            "memory_indexer_state": _wam._resolve_memory_indexer_state(),
            "memory_indexer_queue_size": _wam._resolve_memory_indexer_queue_size(),
        }
        if _rate_limiter_stats is not None:
            result["telegram_rate_limiter"] = _rate_limiter_stats
        return result

    # ── /api/health/deep ────────────────────────────────────────────────────

    @router.get("/api/health/deep")
    async def get_health_deep() -> dict:
        """Расширенная диагностика Краба — структурированный JSON для Dashboard V4.

        Зеркало !health deep (Wave 29-EE), но возвращает dict вместо markdown.
        Включает: krab process, openclaw, lm_studio, archive_db,
        reminders, memory_validator, sigterm_recent_count, system,
        + Session 24 (8f0da60): sentry, mcp_servers, cf_tunnel, error_rate_5m.
        """
        from ...core.health_deep_collector import collect_health_deep

        userbot = ctx.get_dep("userbot")
        session_start = getattr(userbot, "_session_start_time", None) if userbot else None
        return await collect_health_deep(session_start_time=session_start)

    # ── /api/v1/health ──────────────────────────────────────────────────────

    @router.get("/api/v1/health")
    async def health_v1() -> dict:
        """Versioned health endpoint для внешних мониторов."""
        try:
            health = await ctx.collect_runtime_lite()
            return {
                "ok": True,
                "version": "1",
                "status": health.get("status", "unknown"),
                "telegram": health.get("telegram_userbot_state", "unknown"),
                "gateway": health.get("openclaw_auth_state", "unknown"),
                "uptime_probe": "pass",
            }
        except Exception as exc:
            return {"ok": False, "version": "1", "error": str(exc)}

    # ── /api/ecosystem/health ───────────────────────────────────────────────

    @router.get("/api/ecosystem/health")
    async def ecosystem_health() -> dict:
        """[R11] Расширенный health-отчет 3-проектной экосистемы с метриками ресурсов."""
        from ...core.ecosystem_health import EcosystemHealthService

        health_service = ctx.get_dep("health_service")
        if not health_service:
            # Fallback для совместимости, если сервис не в депсах
            router_dep = ctx.deps["router"]
            openclaw = ctx.get_dep("openclaw_client")
            voice_gateway = ctx.get_dep("voice_gateway_client")
            krab_ear = ctx.get_dep("krab_ear_client")
            health_service = EcosystemHealthService(
                router=router_dep,
                openclaw_client=openclaw,
                voice_gateway_client=voice_gateway,
                krab_ear_client=krab_ear,
            )
        report = await health_service.collect()
        return {"ok": True, "report": report}

    # ── /api/ecosystem/health/debug ─────────────────────────────────────────

    @router.get("/api/ecosystem/health/debug")
    async def ecosystem_health_debug(section: str = "") -> dict:
        """Raw health collector output + full dict для diagnose.

        Query params:
        - section: filter one section (session_10, session_12, runtime_route, etc.)
        """
        try:
            health_svc = ctx.get_dep("health_service")
            if health_svc is None:
                router_dep = ctx.get_dep("router")
                if router_dep is None:
                    return {"error": "router_not_found_in_deps"}
                from ...core.ecosystem_health import EcosystemHealthService

                health_svc = EcosystemHealthService(router=router_dep)
            direct = health_svc._collect_session_12_stats()
            full = await health_svc.collect()

            response: dict = {
                "direct": direct,
                "full_has_session_12": "session_12" in full,
                "full_keys": list(full.keys()),
            }

            if section:
                response["section_filter"] = section
                response["full_section"] = full.get(section)
            else:
                response["full_session_12"] = full.get("session_12")

            return response
        except Exception as exc:
            import traceback

            return {"error": str(exc), "trace": traceback.format_exc()[:500]}

    # ── /api/ecosystem/health/export ────────────────────────────────────────

    @router.get("/api/ecosystem/health/export")
    async def ecosystem_health_export() -> FileResponse:
        """Экспортирует расширенный ecosystem health report в JSON-файл."""
        from ...core.ecosystem_health import EcosystemHealthService

        router_dep = ctx.deps["router"]
        openclaw = ctx.get_dep("openclaw_client")
        voice_gateway = ctx.get_dep("voice_gateway_client")
        krab_ear = ctx.get_dep("krab_ear_client")
        payload = await EcosystemHealthService(
            router=router_dep,
            openclaw_client=openclaw,
            voice_gateway_client=voice_gateway,
            krab_ear_client=krab_ear,
        ).collect()
        ops_dir = Path("artifacts/ops")
        ops_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        out_path = ops_dir / f"ecosystem_health_web_{stamp}.json"
        with out_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        return FileResponse(
            str(out_path),
            media_type="application/json",
            filename=out_path.name,
        )

    # ── /api/network/probes ─────────────────────────────────────────────────
    # Wave 163: восстановление endpoint после Session 47 refactor — внешние
    # monitoring скрипты и Prometheus alerts по-прежнему опрашивают split-brain
    # state. Возвращает live snapshot:
    #   main_app.split_brain        — bool (последний get_state probe)
    #   main_app.last_event_age_sec — int  (с момента последнего _process_message)
    #   dispatcher_tick.starved     — bool (Wave 63-C staleness threshold)
    #   pyrogram.disconnects_total  — int  (Wave 142 reconnect storm counter)
    #   pyrogram.session_label      — str  (текущая active session label)

    @router.get("/api/network/probes")
    async def get_network_probes() -> dict:
        """Wave 163: split-brain + pyrogram метрики для внешнего мониторинга.

        Источники данных:
        - userbot._last_telegram_event_ts / _last_dispatcher_tick_ts
        - userbot._last_get_state_probe (если установлен network_watchdog)
        - prometheus_metrics._PYROGRAM_DISCONNECTS_COUNTER / session label
        - userbot.network_watchdog._check_dispatcher_starved (Wave 63-C)
        """
        now = time.time()
        userbot = ctx.get_dep("kraab_userbot")

        # ── main_app section ────────────────────────────────────────────────
        last_event_ts = float(getattr(userbot, "_last_telegram_event_ts", 0.0) or 0.0)
        last_event_age_sec: int = int(now - last_event_ts) if last_event_ts > 0 else -1
        # split_brain: последний get_state probe пометил подозрение, либо
        # network_watchdog выставил атрибут (Wave 63-A).
        split_brain = False
        try:
            probe = getattr(userbot, "_last_get_state_probe", None)
            if probe is not None:
                split_brain = bool(getattr(probe, "split_brain_suspected", False))
            else:
                split_brain = bool(getattr(userbot, "_split_brain_suspected", False))
        except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-open
            logger.warning("network_probes_split_brain_read_failed", error=str(exc)[:200])
            split_brain = False

        # ── dispatcher_tick section ────────────────────────────────────────
        dispatcher_starved = False
        try:
            # Используем общий helper из watchdog — единственный источник истины
            # для staleness threshold (env KRAB_DISPATCHER_TICK_STALENESS_SEC).
            from ...userbot.network_watchdog import _check_dispatcher_starved

            if userbot is not None:
                dispatcher_starved = bool(_check_dispatcher_starved(userbot, now=now))
        except Exception as exc:  # noqa: BLE001 — fail-open, не ломаем endpoint
            logger.warning("network_probes_dispatcher_check_failed", error=str(exc)[:200])
            dispatcher_starved = False

        last_dispatcher_tick_ts = float(getattr(userbot, "_last_dispatcher_tick_ts", 0.0) or 0.0)
        dispatcher_tick_age_sec: int = (
            int(now - last_dispatcher_tick_ts) if last_dispatcher_tick_ts > 0 else -1
        )
        dispatcher_tick_count = int(getattr(userbot, "_dispatcher_tick_count", 0) or 0)

        # Session 54 Task C: raw_update_tick секция удалена. on_raw_update
        # handler был не reliable (UpdateShort(UpdateNewMessage) — dominant
        # traffic — bypass'ил raw handlers). Liveness теперь через
        # Client.last_update_time в network_watchdog (S53 hotfix3).

        # ── pyrogram section (Wave 142) ────────────────────────────────────
        disconnects_total = 0
        session_label = "unknown"
        try:
            from ...core.prometheus_metrics import (
                _PYROGRAM_DISCONNECTS_COUNTER,
                get_pyrogram_session_label,
            )

            disconnects_total = int(sum(_PYROGRAM_DISCONNECTS_COUNTER.values()))
            session_label = str(get_pyrogram_session_label() or "unknown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("network_probes_pyrogram_read_failed", error=str(exc)[:200])

        return {
            "ok": True,
            "timestamp": int(now),
            "main_app": {
                "split_brain": split_brain,
                "last_event_age_sec": last_event_age_sec,
                "last_event_ts": last_event_ts,
            },
            "dispatcher_tick": {
                "starved": dispatcher_starved,
                "age_sec": dispatcher_tick_age_sec,
                "count": dispatcher_tick_count,
            },
            "pyrogram": {
                "disconnects_total": disconnects_total,
                "session_label": session_label,
            },
        }

    return router
