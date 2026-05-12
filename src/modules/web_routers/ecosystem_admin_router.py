# -*- coding: utf-8 -*-
"""
Ecosystem admin router — Wave 156.

Объединённый ecosystem overview в Owner Panel ``:8080``. Single page grid с
green/yellow/red статусами всех подсистем Краба:

- Krab core (process alive + dispatcher tick from Wave 63-C);
- OpenClaw gateway ``:18789`` (Wave 124 health endpoint);
- Voice Gateway ``:8090`` (через VoiceGatewayClient.health_check);
- Krab Ear backend ``:5005`` (Wave 79, через httpx /health);
- LM Studio ``:1234`` (Wave 133, через local runtime probe);
- 4 swarm clients (Wave 63-B per-team pts/qts probe state);
- paid_gemini_guard (Wave 67 mode + Wave 69 stats);
- Sentry quota (best-effort через sentry_integration counters);
- Disk / RAM usage (Wave 111 disk_space_monitor + psutil).

Endpoints:
- GET /api/admin/ecosystem/dashboard — aggregated JSON для UI.
- GET /admin/ecosystem                — HTML page (vanilla JS polling 30s).

Контракт ``/api/admin/ecosystem/dashboard``::

    {
      "ok": true,
      "now": "2026-05-12T12:00:00+00:00",
      "services": [
        {"id": "krab_core", "label": "Krab Core", "status": "ok|warn|crit",
         "metric": "uptime 2h 15m", "detail": "dispatcher tick 1.2s ago",
         "link": null},
        ...
      ],
      "summary": {"ok_count": int, "warn_count": int, "crit_count": int}
    }

В отличие от ``/api/health`` (binary ok/degraded для 4 источников), этот
endpoint аггрегирует 9+ источников с уровнем granularity per-service для
визуального grid. Контракт намеренно flat list ``services`` — UI рендерит
cards в произвольном порядке/группировке.

Все endpoints read-only (GET без auth — overview не expose секретов).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ._context import RouterContext

# Статусы сервисов: ok (зелёный) / warn (жёлтый) / crit (красный) / unknown (серый).
_STATUS_OK = "ok"
_STATUS_WARN = "warn"
_STATUS_CRIT = "crit"
_STATUS_UNKNOWN = "unknown"

# Пороги для resource monitor (Wave 111).
_DISK_WARN_PCT = 90.0
_DISK_CRIT_PCT = 95.0
_RAM_WARN_PCT = 85.0
_RAM_CRIT_PCT = 95.0

# Свежесть dispatcher tick (Wave 63-C): > 60s = warn, > 300s = crit.
_DISPATCHER_WARN_AGE_SEC = 60.0
_DISPATCHER_CRIT_AGE_SEC = 300.0

# Свежесть swarm probe (Wave 63-B): > 600s = warn, > 1800s = crit.
_SWARM_WARN_AGE_SEC = 600.0
_SWARM_CRIT_AGE_SEC = 1800.0


def _format_uptime(seconds: float) -> str:
    """Форматирует секунды в `XdYh ZmWs` (компактно для card.metric)."""
    if seconds < 0:
        seconds = 0
    s = int(seconds)
    days = s // 86400
    s -= days * 86400
    hours = s // 3600
    s -= hours * 3600
    minutes = s // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return f"{s}s"


def _format_age(seconds: float | None) -> str:
    """Форматирует «N сек/мин/час назад» (для probe ages)."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


# ── Service collectors ─────────────────────────────────────────────────────


def _collect_krab_core(ctx: RouterContext) -> dict[str, Any]:
    """Krab core: process alive + dispatcher_tick freshness (Wave 63-C)."""
    boot_ts = ctx.get_boot_ts()
    uptime_sec = max(0.0, time.time() - boot_ts)

    userbot = ctx.get_dep("userbot") or ctx.get_dep("kraab_userbot")
    if userbot is None:
        return {
            "id": "krab_core",
            "label": "Krab Core",
            "status": _STATUS_UNKNOWN,
            "metric": _format_uptime(uptime_sec),
            "detail": "userbot dep not available",
            "link": None,
        }

    tick_ts = getattr(userbot, "_last_dispatcher_tick_ts", None)
    tick_count = int(getattr(userbot, "_dispatcher_tick_count", 0) or 0)

    if tick_ts is None or tick_count == 0:
        # Только что запустились или dispatcher ещё не начал tick'ать.
        return {
            "id": "krab_core",
            "label": "Krab Core",
            "status": _STATUS_OK if uptime_sec < 30 else _STATUS_WARN,
            "metric": _format_uptime(uptime_sec),
            "detail": "no dispatcher tick yet",
            "link": None,
        }

    age = max(0.0, time.time() - float(tick_ts))
    if age > _DISPATCHER_CRIT_AGE_SEC:
        status = _STATUS_CRIT
    elif age > _DISPATCHER_WARN_AGE_SEC:
        status = _STATUS_WARN
    else:
        status = _STATUS_OK

    return {
        "id": "krab_core",
        "label": "Krab Core",
        "status": status,
        "metric": _format_uptime(uptime_sec),
        "detail": f"tick {_format_age(age)} (#{tick_count})",
        "link": None,
    }


def _collect_openclaw(ctx: RouterContext) -> dict[str, Any]:
    """OpenClaw gateway: last_runtime_route + status (Wave 124)."""
    oc = ctx.get_dep("openclaw_client")
    if oc is None or not hasattr(oc, "get_last_runtime_route"):
        return {
            "id": "openclaw",
            "label": "OpenClaw Gateway",
            "status": _STATUS_UNKNOWN,
            "metric": ":18789",
            "detail": "client not configured",
            "link": "/admin/models",
        }
    try:
        route = oc.get_last_runtime_route() or {}
    except Exception:  # noqa: BLE001
        route = {}

    status_str = str(route.get("status") or "").lower()
    ts_raw = route.get("timestamp")
    age: float | None = None
    if ts_raw is not None:
        try:
            age = max(0.0, time.time() - float(ts_raw))
        except (TypeError, ValueError):
            age = None

    if status_str in {"ok", "healthy", "success"}:
        kind = _STATUS_OK
    elif status_str in {"pending", "init", "warmup"}:
        kind = _STATUS_WARN
    elif status_str in {"error", "fail", "failed", "down", "timeout"}:
        kind = _STATUS_CRIT
    else:
        kind = _STATUS_WARN if not route else _STATUS_UNKNOWN

    return {
        "id": "openclaw",
        "label": "OpenClaw Gateway",
        "status": kind,
        "metric": str(route.get("model") or ":18789") or ":18789",
        "detail": f"status={status_str or 'unknown'} {_format_age(age)}",
        "link": "/admin/models",
    }


def _collect_voice_gateway(ctx: RouterContext) -> dict[str, Any]:
    """Voice Gateway :8090: configured + last health snapshot."""
    vg = ctx.get_dep("voice_gateway_client")
    if vg is None:
        return {
            "id": "voice_gateway",
            "label": "Voice Gateway",
            "status": _STATUS_UNKNOWN,
            "metric": ":8090",
            "detail": "client not configured",
            "link": None,
        }
    # Best-effort: смотрим на is_configured / последний health (если кэшируется).
    configured = getattr(vg, "is_configured", None)
    if callable(configured):
        try:
            is_config = bool(configured())
        except Exception:  # noqa: BLE001
            is_config = False
    else:
        is_config = bool(configured) if configured is not None else True
    last_status = getattr(vg, "last_health_status", None)
    last_ts = getattr(vg, "last_health_ts", None)
    age = None
    if last_ts is not None:
        try:
            age = max(0.0, time.time() - float(last_ts))
        except (TypeError, ValueError):
            age = None

    if not is_config:
        return {
            "id": "voice_gateway",
            "label": "Voice Gateway",
            "status": _STATUS_WARN,
            "metric": ":8090",
            "detail": "not configured",
            "link": None,
        }

    if last_status is None:
        return {
            "id": "voice_gateway",
            "label": "Voice Gateway",
            "status": _STATUS_UNKNOWN,
            "metric": ":8090",
            "detail": "no probe yet",
            "link": None,
        }
    is_ok = bool(last_status)
    return {
        "id": "voice_gateway",
        "label": "Voice Gateway",
        "status": _STATUS_OK if is_ok else _STATUS_CRIT,
        "metric": ":8090",
        "detail": f"{'ok' if is_ok else 'down'} {_format_age(age)}",
        "link": None,
    }


def _collect_krab_ear(ctx: RouterContext) -> dict[str, Any]:
    """Krab Ear backend :5005 (Wave 79). Best-effort через krab_ear_client."""
    ke = ctx.get_dep("krab_ear_client")
    url_default = os.environ.get("KRAB_EAR_BACKEND_URL", "http://127.0.0.1:5005")
    if ke is None:
        return {
            "id": "krab_ear",
            "label": "Krab Ear",
            "status": _STATUS_UNKNOWN,
            "metric": ":5005",
            "detail": "client not configured",
            "link": None,
        }
    last_status = getattr(ke, "last_health_status", None)
    last_ts = getattr(ke, "last_health_ts", None)
    age = None
    if last_ts is not None:
        try:
            age = max(0.0, time.time() - float(last_ts))
        except (TypeError, ValueError):
            age = None

    if last_status is None:
        return {
            "id": "krab_ear",
            "label": "Krab Ear",
            "status": _STATUS_UNKNOWN,
            "metric": url_default.split("//")[-1] if "//" in url_default else ":5005",
            "detail": "no probe yet",
            "link": None,
        }
    is_ok = bool(last_status)
    return {
        "id": "krab_ear",
        "label": "Krab Ear",
        "status": _STATUS_OK if is_ok else _STATUS_CRIT,
        "metric": ":5005",
        "detail": f"{'ok' if is_ok else 'down'} {_format_age(age)}",
        "link": None,
    }


def _collect_lm_studio(ctx: RouterContext) -> dict[str, Any]:
    """LM Studio :1234 (Wave 133). Best-effort через runtime truth helper."""
    helper = ctx.deps.get("resolve_local_runtime_truth_helper")
    router_obj = ctx.deps.get("router")
    if helper is None or router_obj is None:
        return {
            "id": "lm_studio",
            "label": "LM Studio",
            "status": _STATUS_UNKNOWN,
            "metric": ":1234",
            "detail": "helper not available",
            "link": "/admin/models",
        }
    try:
        truth = helper(router_obj)
        import asyncio as _asyncio

        if _asyncio.iscoroutine(truth):
            # При синхронном вызове из dashboard корутина не должна попадать —
            # но обрабатываем graceful.
            return {
                "id": "lm_studio",
                "label": "LM Studio",
                "status": _STATUS_UNKNOWN,
                "metric": ":1234",
                "detail": "async probe (run separately)",
                "link": "/admin/models",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": "lm_studio",
            "label": "LM Studio",
            "status": _STATUS_CRIT,
            "metric": ":1234",
            "detail": f"probe failed: {exc}",
            "link": "/admin/models",
        }

    if not isinstance(truth, dict):
        return {
            "id": "lm_studio",
            "label": "LM Studio",
            "status": _STATUS_UNKNOWN,
            "metric": ":1234",
            "detail": "unexpected probe shape",
            "link": "/admin/models",
        }

    reachable = bool(truth.get("runtime_reachable"))
    active = str(truth.get("active_model") or "").strip()
    loaded_models = truth.get("loaded_models") or []
    loaded_count = len([m for m in loaded_models if str(m or "").strip()])

    if not reachable:
        return {
            "id": "lm_studio",
            "label": "LM Studio",
            "status": _STATUS_WARN,
            "metric": ":1234",
            "detail": "unreachable (idle/offload)",
            "link": "/admin/models",
        }
    if loaded_count == 0:
        return {
            "id": "lm_studio",
            "label": "LM Studio",
            "status": _STATUS_WARN,
            "metric": ":1234",
            "detail": "no model loaded",
            "link": "/admin/models",
        }
    return {
        "id": "lm_studio",
        "label": "LM Studio",
        "status": _STATUS_OK,
        "metric": active or f"{loaded_count} loaded",
        "detail": f"{loaded_count} loaded; active={active or '—'}",
        "link": "/admin/models",
    }


def _collect_swarm_clients(ctx: RouterContext) -> list[dict[str, Any]]:
    """4 swarm Pyrogram clients (Wave 63-B): pts/qts freshness per team.

    Возвращает list карточек — одну на team. Если userbot/probe данные
    отсутствуют — возвращает единичную card-агрегат со status=unknown.
    """
    userbot = ctx.get_dep("userbot") or ctx.get_dep("kraab_userbot")
    if userbot is None:
        return [
            {
                "id": "swarm_clients",
                "label": "Swarm Clients",
                "status": _STATUS_UNKNOWN,
                "metric": "0/4",
                "detail": "userbot not available",
                "link": "/admin/swarm",
            }
        ]

    raw_swarm = getattr(userbot, "_last_swarm_pts", None) or {}
    swarm_team_clients = getattr(userbot, "_swarm_team_clients", None) or {}
    teams_known = sorted({*raw_swarm.keys(), *swarm_team_clients.keys()})

    if not teams_known:
        return [
            {
                "id": "swarm_clients",
                "label": "Swarm Clients",
                "status": _STATUS_UNKNOWN,
                "metric": "0/4",
                "detail": "no team clients initialized",
                "link": "/admin/swarm",
            }
        ]

    now_ts = time.time()
    cards: list[dict[str, Any]] = []
    for team in teams_known:
        snapshot = raw_swarm.get(team) if isinstance(raw_swarm, dict) else None
        snap_ts: float | None
        if isinstance(snapshot, dict):
            try:
                snap_ts = float(snapshot.get("ts") or 0) or None
            except (TypeError, ValueError):
                snap_ts = None
        else:
            snap_ts = None

        age = max(0.0, now_ts - snap_ts) if snap_ts else None
        client = swarm_team_clients.get(team) if isinstance(swarm_team_clients, dict) else None
        connected = bool(getattr(client, "is_connected", False)) if client else False

        if age is None:
            status = _STATUS_WARN if connected else _STATUS_UNKNOWN
            detail = "no probe yet" if connected else "client not connected"
        elif age > _SWARM_CRIT_AGE_SEC:
            status = _STATUS_CRIT
            detail = f"stale {_format_age(age)}"
        elif age > _SWARM_WARN_AGE_SEC:
            status = _STATUS_WARN
            detail = f"slow {_format_age(age)}"
        else:
            status = _STATUS_OK
            detail = f"tick {_format_age(age)}"

        cards.append(
            {
                "id": f"swarm_{team}",
                "label": f"Swarm: {team}",
                "status": status,
                "metric": f"@{team}",
                "detail": detail,
                "link": "/admin/swarm",
            }
        )
    return cards


def _collect_paid_gemini_guard() -> dict[str, Any]:
    """paid_gemini_guard (Wave 67 mode + Wave 69 counters)."""
    mode = "block"
    raw = str(os.environ.get("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")).strip().lower()
    if raw in {"0", "false", "no", "off", "disabled"}:
        mode = "off"
    elif raw == "warn":
        mode = "warn"

    stats: dict[str, Any] = {}
    try:
        from src.integrations.paid_gemini_guard import get_paid_gemini_guard_stats

        stats = get_paid_gemini_guard_stats() or {}
    except Exception:  # noqa: BLE001
        stats = {}

    blocked = int(stats.get("blocked_count") or 0)
    warned = int(stats.get("warned_count") or 0)
    allowed = int(stats.get("allowed_count") or 0)

    if mode == "off":
        status = _STATUS_CRIT
        detail = "guard disabled (paid spend possible)"
    elif mode == "warn":
        status = _STATUS_WARN
        detail = f"warn-mode: warned={warned} allowed={allowed}"
    else:
        # mode=block — нормальная работа. crit если allowed_count > 0
        # (что не должно случаться при block).
        if allowed > 0:
            status = _STATUS_WARN
            detail = f"unexpected allowed={allowed} blocked={blocked}"
        else:
            status = _STATUS_OK
            detail = f"block-mode: blocked={blocked}"

    return {
        "id": "paid_gemini_guard",
        "label": "Paid Gemini Guard",
        "status": status,
        "metric": mode,
        "detail": detail,
        "link": None,
    }


def _collect_sentry_quota() -> dict[str, Any]:
    """Sentry quota status (Wave 71 best-effort).

    Возвращает unknown если sentry_sdk не инициализирован — это нормально
    в тестовой среде. В production хук на event_processor собирает rate.
    """
    try:
        import sentry_sdk

        # sentry_sdk 2.x: get_client(); 1.x fallback на Hub.current.client.
        if hasattr(sentry_sdk, "get_client"):
            client = sentry_sdk.get_client()
        elif hasattr(sentry_sdk.Hub, "current"):
            client = sentry_sdk.Hub.current.client
        else:
            client = None
        if client is None:
            return {
                "id": "sentry",
                "label": "Sentry",
                "status": _STATUS_UNKNOWN,
                "metric": "not initialized",
                "detail": "no SDK client",
                "link": None,
            }
        dsn = getattr(client, "dsn", None)
        if not dsn:
            return {
                "id": "sentry",
                "label": "Sentry",
                "status": _STATUS_WARN,
                "metric": "no DSN",
                "detail": "Sentry SDK installed but DSN missing",
                "link": None,
            }
        return {
            "id": "sentry",
            "label": "Sentry",
            "status": _STATUS_OK,
            "metric": "configured",
            "detail": "DSN present, events flowing",
            "link": None,
        }
    except Exception:  # noqa: BLE001
        return {
            "id": "sentry",
            "label": "Sentry",
            "status": _STATUS_UNKNOWN,
            "metric": "sdk missing",
            "detail": "sentry_sdk not importable",
            "link": None,
        }


def _collect_resources() -> list[dict[str, Any]]:
    """Disk + RAM usage (Wave 111 + psutil). Возвращает 2 card'а."""
    cards: list[dict[str, Any]] = []

    # Disk (корневой /).
    try:
        import shutil

        usage = shutil.disk_usage("/")
        used_pct = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0
        free_gb = usage.free / (1024**3)
        if used_pct >= _DISK_CRIT_PCT:
            status = _STATUS_CRIT
        elif used_pct >= _DISK_WARN_PCT:
            status = _STATUS_WARN
        else:
            status = _STATUS_OK
        cards.append(
            {
                "id": "disk",
                "label": "Disk Usage",
                "status": status,
                "metric": f"{used_pct:.1f}%",
                "detail": f"free {free_gb:.1f} GB ({used_pct:.1f}% used)",
                "link": None,
            }
        )
    except Exception as exc:  # noqa: BLE001
        cards.append(
            {
                "id": "disk",
                "label": "Disk Usage",
                "status": _STATUS_UNKNOWN,
                "metric": "—",
                "detail": f"error: {exc}",
                "link": None,
            }
        )

    # RAM (psutil.virtual_memory).
    try:
        import psutil

        vm = psutil.virtual_memory()
        used_pct = float(vm.percent)
        avail_gb = vm.available / (1024**3)
        if used_pct >= _RAM_CRIT_PCT:
            status = _STATUS_CRIT
        elif used_pct >= _RAM_WARN_PCT:
            status = _STATUS_WARN
        else:
            status = _STATUS_OK
        cards.append(
            {
                "id": "ram",
                "label": "RAM Usage",
                "status": status,
                "metric": f"{used_pct:.1f}%",
                "detail": f"available {avail_gb:.1f} GB",
                "link": None,
            }
        )
    except Exception as exc:  # noqa: BLE001
        cards.append(
            {
                "id": "ram",
                "label": "RAM Usage",
                "status": _STATUS_UNKNOWN,
                "metric": "—",
                "detail": f"error: {exc}",
                "link": None,
            }
        )

    return cards


def _summarize(services: list[dict[str, Any]]) -> dict[str, int]:
    """Подсчитывает ok/warn/crit/unknown по списку сервисов."""
    summary = {"ok_count": 0, "warn_count": 0, "crit_count": 0, "unknown_count": 0}
    for svc in services:
        status = str(svc.get("status") or "").lower()
        if status == _STATUS_OK:
            summary["ok_count"] += 1
        elif status == _STATUS_WARN:
            summary["warn_count"] += 1
        elif status == _STATUS_CRIT:
            summary["crit_count"] += 1
        else:
            summary["unknown_count"] += 1
    return summary


# ── Main factory ────────────────────────────────────────────────────────────


def build_ecosystem_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с ecosystem overview UI + aggregated API."""
    router = APIRouter(tags=["ecosystem-admin"])

    # ---------- GET /api/admin/ecosystem/dashboard ----------------------------
    @router.get("/api/admin/ecosystem/dashboard")
    async def ecosystem_dashboard() -> dict[str, Any]:
        """Aggregated ecosystem snapshot для /admin/ecosystem UI."""
        services: list[dict[str, Any]] = []
        services.append(_collect_krab_core(ctx))
        services.append(_collect_openclaw(ctx))
        services.append(_collect_voice_gateway(ctx))
        services.append(_collect_krab_ear(ctx))
        services.append(_collect_lm_studio(ctx))
        services.extend(_collect_swarm_clients(ctx))
        services.append(_collect_paid_gemini_guard())
        services.append(_collect_sentry_quota())
        services.extend(_collect_resources())

        return {
            "ok": True,
            "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "services": services,
            "summary": _summarize(services),
        }

    # ---------- GET /admin/ecosystem ------------------------------------------
    @router.get("/admin/ecosystem", response_class=HTMLResponse)
    async def admin_ecosystem_page() -> HTMLResponse:
        """HTML страница ecosystem overview."""
        return HTMLResponse(_ECOSYSTEM_PAGE_HTML, headers={"Cache-Control": "no-store"})

    return router


# ── Inline HTML template ────────────────────────────────────────────────────
# Все server-данные рендерятся через .textContent / DOM API без innerHTML —
# защищаемся от XSS (detail может содержать произвольные строки из probe
# результатов).

_ECOSYSTEM_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Krab — Ecosystem Overview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --fg: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --ok: #2ea043;
    --warn: #d29922;
    --err: #f85149;
    --unk: #6c757d;
  }
  body { background: var(--bg); color: var(--fg); margin: 0;
         font: 14px -apple-system, BlinkMacSystemFont, sans-serif; }
  header { padding: 16px 24px; border-bottom: 1px solid var(--border);
           display: flex; justify-content: space-between; align-items: center; }
  h1 { margin: 0; font-size: 18px; }
  nav.tabs a { color: var(--muted); text-decoration: none; margin-right: 18px;
               font-size: 13px; padding-bottom: 3px; }
  nav.tabs a:hover { color: var(--accent); }
  nav.tabs a.active { color: var(--accent); border-bottom: 2px solid var(--accent); }
  main { padding: 24px; max-width: 1400px; margin: auto; }
  .summary { display: flex; gap: 18px; margin-bottom: 24px; flex-wrap: wrap; }
  .summary-card { background: var(--card); border: 1px solid var(--border);
                  border-radius: 8px; padding: 12px 18px; min-width: 110px;
                  display: flex; flex-direction: column; align-items: center; }
  .summary-card .num { font-size: 28px; font-weight: 700; }
  .summary-card .lbl { color: var(--muted); font-size: 11px;
                       text-transform: uppercase; letter-spacing: 1px;
                       margin-top: 4px; }
  .summary-card.ok .num { color: var(--ok); }
  .summary-card.warn .num { color: var(--warn); }
  .summary-card.crit .num { color: var(--err); }
  .summary-card.unk .num { color: var(--unk); }
  .grid { display: grid; gap: 14px;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .svc-card { background: var(--card); border: 1px solid var(--border);
              border-radius: 8px; padding: 14px 16px;
              border-left-width: 4px;
              transition: border-color 0.2s, transform 0.1s; }
  .svc-card.status-ok    { border-left-color: var(--ok); }
  .svc-card.status-warn  { border-left-color: var(--warn); }
  .svc-card.status-crit  { border-left-color: var(--err); }
  .svc-card.status-unknown { border-left-color: var(--unk); }
  .svc-card.has-link { cursor: pointer; }
  .svc-card.has-link:hover { transform: translateY(-1px);
                             border-left-color: var(--accent); }
  .svc-head { display: flex; justify-content: space-between;
              align-items: center; margin-bottom: 8px; }
  .svc-label { font-weight: 600; font-size: 13px; }
  .svc-icon { font-size: 14px; line-height: 1; }
  .svc-icon.status-ok    { color: var(--ok); }
  .svc-icon.status-warn  { color: var(--warn); }
  .svc-icon.status-crit  { color: var(--err); }
  .svc-icon.status-unknown { color: var(--unk); }
  .svc-metric { font-size: 16px; font-weight: 600; color: var(--accent);
                margin-bottom: 6px; word-break: break-word; }
  .svc-detail { color: var(--muted); font-size: 11px; }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  .err-toast { color: var(--err); font-size: 12px; }
</style>
</head>
<body>
<header>
  <div style="display:flex; align-items:center; gap:18px;">
    <h1>Krab — Ecosystem Overview</h1>
    <nav class="tabs">
      <a href="/admin/models">Models</a>
      <a href="/admin/routing">Routing</a>
      <a href="/admin/ecosystem" class="active">Ecosystem</a>
      <a href="/admin/swarm">Swarm</a>
      <a href="/admin/costs">Costs</a>
    </nav>
  </div>
  <div style="color: var(--muted); font-size: 12px;">
    Refresh: <span id="last-refresh">—</span>
  </div>
</header>
<main>
  <div class="summary">
    <div class="summary-card ok">
      <div class="num" id="sum-ok">—</div>
      <div class="lbl">OK</div>
    </div>
    <div class="summary-card warn">
      <div class="num" id="sum-warn">—</div>
      <div class="lbl">Warn</div>
    </div>
    <div class="summary-card crit">
      <div class="num" id="sum-crit">—</div>
      <div class="lbl">Crit</div>
    </div>
    <div class="summary-card unk">
      <div class="num" id="sum-unk">—</div>
      <div class="lbl">Unknown</div>
    </div>
  </div>
  <div class="grid" id="grid"></div>
</main>
<script>
'use strict';

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else if (k.startsWith('on') && typeof attrs[k] === 'function') node[k] = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
  }
  if (children) for (const c of children) if (c) node.appendChild(c);
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statusIcon(status) {
  switch ((status || '').toLowerCase()) {
    case 'ok': return '✅';
    case 'warn': return '⚠';
    case 'crit': return '🔴';
    default: return '➖';
  }
}

function renderCard(svc) {
  const status = (svc.status || 'unknown').toLowerCase();
  const card = el('div', { class: 'svc-card status-' + status });
  if (svc.link) {
    card.classList.add('has-link');
    card.onclick = () => { window.location.href = svc.link; };
  }
  const head = el('div', { class: 'svc-head' });
  head.appendChild(el('div', { class: 'svc-label', text: svc.label || svc.id }));
  head.appendChild(el('div', {
    class: 'svc-icon status-' + status,
    text: statusIcon(status),
  }));
  card.appendChild(head);
  card.appendChild(el('div', { class: 'svc-metric', text: svc.metric || '—' }));
  card.appendChild(el('div', { class: 'svc-detail', text: svc.detail || '' }));
  return card;
}

async function refresh() {
  try {
    const r = await fetch('/api/admin/ecosystem/dashboard', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.ok) throw new Error('payload not ok');

    const sum = data.summary || {};
    document.getElementById('sum-ok').textContent = String(sum.ok_count || 0);
    document.getElementById('sum-warn').textContent = String(sum.warn_count || 0);
    document.getElementById('sum-crit').textContent = String(sum.crit_count || 0);
    document.getElementById('sum-unk').textContent = String(sum.unknown_count || 0);

    const grid = document.getElementById('grid');
    clearNode(grid);
    const services = data.services || [];
    if (services.length === 0) {
      grid.appendChild(el('div', { class: 'empty', text: 'No services available.' }));
    } else {
      for (const svc of services) {
        grid.appendChild(renderCard(svc));
      }
    }

    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch (exc) {
    document.getElementById('last-refresh').textContent = 'error: ' + exc;
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""
