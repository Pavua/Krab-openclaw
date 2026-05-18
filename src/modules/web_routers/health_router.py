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
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...core.logger import get_logger
from ._context import RouterContext

logger = get_logger(__name__)

# ── S58: local_draft_verifier stats endpoint ────────────────────────────────
# Pattern matching the structlog ConsoleRenderer output written to
# krab_main.log. Format (with ANSI codes stripped):
#   "2026-05-18 03:17:39 [info     ] <event_name>     key=value key=value ..."
# We grep for "local_draft_verify_divergence_score" events from the last 24h
# and extract: timestamp, divergence_score, local_model, request_id.

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_KV_RE = re.compile(r"(\w+)=('([^']*)'|\"([^\"]*)\"|(\S+))")
_LOCAL_DRAFT_EVENT = "local_draft_verify_divergence_score"

# In-memory cache to avoid heavy log re-parse per HTTP request (60s TTL).
# Holder dict shared at module level; tests can clear via _LDV_CACHE.clear().
_LDV_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_LDV_CACHE_TTL_SEC = 60.0
_LDV_LOOKBACK_SEC = 24 * 3600


def _resolve_log_file_path() -> Path:
    """Return the krab_main.log path, honoring KRAB_LOG_FILE override."""
    raw = os.environ.get("KRAB_LOG_FILE")
    if raw:
        return Path(raw).expanduser()
    base = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    base_dir = Path(base).expanduser() if base else Path.home() / ".openclaw" / "krab_runtime_state"
    return base_dir / "krab_main.log"


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Extract structlog event_name + key=value pairs from one log line.

    Returns dict with at least ``event`` key on success, ``None`` if line
    isn't a structlog event (e.g. pyrogram raw output, header banners).
    Robust to ANSI codes and malformed lines.
    """
    try:
        clean = _ANSI_RE.sub("", line).strip()
    except Exception:  # noqa: BLE001 — corrupt encoding etc.
        return None
    if not clean:
        return None
    ts_match = _TIMESTAMP_RE.match(clean)
    if not ts_match:
        return None
    timestamp = ts_match.group(1)
    # After timestamp: "[level     ] event_name     key=value ..."
    rest = clean[len(timestamp) :].lstrip()
    # Strip "[level    ]"
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            return None
        rest = rest[end + 1 :].lstrip()
    # Event name = first whitespace-delimited token. Structlog pads with
    # spaces (e.g. "local_draft_verify_divergence_score    key=value").
    parts = rest.split(None, 1)
    if not parts:
        return None
    event = parts[0]
    kvs: dict[str, Any] = {"event": event, "_timestamp": timestamp}
    if len(parts) > 1:
        for match in _KV_RE.finditer(parts[1]):
            key = match.group(1)
            # Prefer quoted captures, fall back to raw token
            value = match.group(3) or match.group(4) or match.group(5) or ""
            kvs[key] = value
    return kvs


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bucket_score(score: int) -> str:
    if score <= 2:
        return "0-2"
    if score <= 5:
        return "3-5"
    if score <= 8:
        return "6-8"
    return "9-10"


def _collect_verifier_samples(
    log_path: Path,
    *,
    now: float,
    lookback_sec: int = _LDV_LOOKBACK_SEC,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read log_path, return (samples, warnings).

    samples: list of {"ts": iso_str, "epoch": float, "model": str,
                      "score": int, "request_id": str}
    Each sample comes from a ``local_draft_verify_divergence_score`` event
    whose parsed timestamp is within ``lookback_sec`` of ``now``.
    """
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not log_path.exists():
        warnings.append(f"log_file_missing:{log_path}")
        return samples, warnings
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _LOCAL_DRAFT_EVENT not in line:
                    continue
                parsed = _parse_log_line(line)
                if parsed is None or parsed.get("event") != _LOCAL_DRAFT_EVENT:
                    continue
                ts_str = parsed.get("_timestamp", "")
                try:
                    epoch = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
                if now - epoch > lookback_sec:
                    continue
                score = _coerce_int(parsed.get("divergence_score"))
                if score is None or score < 0 or score > 10:
                    continue
                samples.append(
                    {
                        "ts": ts_str,
                        "epoch": epoch,
                        "model": str(parsed.get("local_model", "") or ""),
                        "score": score,
                        "request_id": str(parsed.get("request_id", "") or ""),
                    }
                )
    except OSError as exc:
        warnings.append(f"log_read_error:{exc}")
    return samples, warnings


def _compute_stats_payload(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the ``stats`` sub-dict from raw samples."""
    histogram = {"0-2": 0, "3-5": 0, "6-8": 0, "9-10": 0}
    scores: list[int] = []
    for sample in samples:
        score = sample["score"]
        histogram[_bucket_score(score)] += 1
        scores.append(score)
    last_10 = sorted(samples, key=lambda s: s["epoch"], reverse=True)[:10]
    last_10_payload = [
        {
            "ts": s["ts"],
            "model": s["model"],
            "score": s["score"],
            "request_id": s["request_id"],
        }
        for s in last_10
    ]
    if scores:
        mean_score = round(statistics.fmean(scores), 2)
        median_score = round(statistics.median(scores), 2)
    else:
        mean_score = None
        median_score = None
    return {
        "total_verified_24h": len(samples),
        "divergence_histogram": histogram,
        "last_10_samples": last_10_payload,
        "mean_score": mean_score,
        "median_score": median_score,
    }


def _verifier_env_truthy() -> bool:
    return str(os.environ.get("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _verifier_sample_rate() -> float:
    raw = os.environ.get("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "0.2")
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return 0.2
    return max(0.0, min(1.0, rate))


def collect_local_draft_verifier_stats(
    *,
    now: float | None = None,
    cache_ttl_sec: float | None = None,
) -> dict[str, Any]:
    """Build the response envelope for /api/admin/local-draft-verifier-stats.

    Cache TTL applies to the ``stats`` block; env flags are always fresh.
    Tests can pass ``cache_ttl_sec=0`` to force re-parse.
    """
    if now is None:
        now = time.time()
    ttl = _LDV_CACHE_TTL_SEC if cache_ttl_sec is None else cache_ttl_sec

    cached_payload = _LDV_CACHE.get("payload")
    cached_ts = float(_LDV_CACHE.get("ts") or 0.0)
    if cached_payload is not None and ttl > 0 and (now - cached_ts) < ttl:
        stats = cached_payload["stats"]
        warnings = list(cached_payload["warnings"])
    else:
        log_path = _resolve_log_file_path()
        samples, warnings = _collect_verifier_samples(log_path, now=now)
        stats = _compute_stats_payload(samples)
        _LDV_CACHE["ts"] = now
        _LDV_CACHE["payload"] = {"stats": stats, "warnings": list(warnings)}

    return {
        "ok": True,
        "enabled": _verifier_env_truthy(),
        "sample_rate": _verifier_sample_rate(),
        "stats": stats,
        "warnings": warnings,
    }


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

    # ── /api/admin/local-draft-verifier-stats (S58) ─────────────────────────
    # Rolling 24h stats sourced from local_draft_verify_divergence_score events
    # in krab_main.log. 60s cache TTL — heavy log parse не на каждый запрос.
    # Robust к отсутствию S57 module: если событий в логе нет, возвращаем
    # zeroed stats + null means.

    @router.get("/api/admin/local-draft-verifier-stats")
    async def get_local_draft_verifier_stats() -> dict:
        """S58: rolling histogram + last-10 samples от S57 verifier событий."""
        try:
            return collect_local_draft_verifier_stats()
        except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-open
            logger.warning("local_draft_verifier_stats_failed", error=str(exc)[:200])
            return {
                "ok": False,
                "enabled": _verifier_env_truthy(),
                "sample_rate": _verifier_sample_rate(),
                "stats": {
                    "total_verified_24h": 0,
                    "divergence_histogram": {"0-2": 0, "3-5": 0, "6-8": 0, "9-10": 0},
                    "last_10_samples": [],
                    "mean_score": None,
                    "median_score": None,
                },
                "warnings": [f"endpoint_error:{type(exc).__name__}"],
            }

    return router
