# -*- coding: utf-8 -*-
"""
Health dashboard router — Wave 186 (Session 48).

Owner-panel страница ``/admin/health`` + JSON API — unified single-pane-of-glass
для всех subsystem статусов. Агрегирует уже существующие endpoint'ы
(/api/health, /api/ecosystem/health, /api/network/probes, /api/admin/voice/status,
/api/admin/memory/stats, /api/admin/cron/list, /api/admin/sentry/dashboard,
/api/admin/db/list) — НЕ делаем re-query DBs / re-probe, только агрегация.

Lightweight implementation:
- Concurrent fan-out через ``asyncio.gather(..., return_exceptions=True)``
- Per-endpoint timeout = 3.0 сек (httpx.AsyncClient)
- 10-секундный in-memory cache → защита от stampede на rapid refresh
- Fail-soft: offline endpoint → пустой dict + флаг ``ok=False``

Endpoints:
- GET /api/admin/health/dashboard — JSON aggregated state (10s cached)
- GET /admin/health                — HTML

Traffic-light logic (top header):
- 🔴 red    — overall status ∈ {error,critical} ИЛИ split_brain ИЛИ Krab offline
- 🟡 yellow — overall status == warning ИЛИ overdue cron'ы ИЛИ Sentry quota ≥ 80%
- 🟢 green  — иначе

См. ``src/modules/web_routers/sentry_admin_router.py`` для общего pattern Wave 164+.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.logger import get_logger

from ._context import RouterContext

logger = get_logger("health_dashboard_router")

# ── Конфигурация ────────────────────────────────────────────────────────────

# Per-endpoint timeout — короткий, чтобы офлайновые endpoint'ы не блокировали.
DEFAULT_TIMEOUT_SEC = 3.0
# TTL для in-memory aggregation cache.
CACHE_TTL_SEC = 10.0
# Базовый URL self-loopback. Адрес owner-панели жёстко 127.0.0.1:8080.
_DEFAULT_PANEL_BASE = "http://127.0.0.1:8080"

# Endpoint table — name → (relative path, weight для traffic light).
# Список не критичен в смысле SLA — каждый endpoint fail-soft возвращает {}.
_AGGREGATED_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("health", "/api/health"),
    ("ecosystem", "/api/ecosystem/health"),
    ("network", "/api/network/probes"),
    ("voice", "/api/admin/voice/status"),
    ("memory", "/api/admin/memory/stats"),
    ("cron", "/api/admin/cron/list"),
    ("sentry", "/api/admin/sentry/dashboard"),
    ("db", "/api/admin/db/list"),
)


# ── In-memory cache (1 slot, простой TTL) ───────────────────────────────────


class _CacheSlot:
    """Простейший single-slot TTL-cache. Защита от stampede на /admin/health."""

    __slots__ = ("ts", "value")

    def __init__(self) -> None:
        self.ts: float = 0.0
        self.value: dict[str, Any] | None = None


# Module-level — один shared cache для всего процесса.
_CACHE = _CacheSlot()


def _cache_get() -> dict[str, Any] | None:
    """Возвращает cached payload, если свежее CACHE_TTL_SEC, иначе None."""
    if _CACHE.value is None:
        return None
    if (time.monotonic() - _CACHE.ts) > CACHE_TTL_SEC:
        return None
    return _CACHE.value


def _cache_put(payload: dict[str, Any]) -> None:
    """Сохраняет payload в cache с текущим timestamp."""
    _CACHE.value = payload
    _CACHE.ts = time.monotonic()


def _cache_clear() -> None:
    """Сбрасывает cache — используется тестами для изоляции."""
    _CACHE.value = None
    _CACHE.ts = 0.0


# ── Aggregation helpers ─────────────────────────────────────────────────────


async def _fetch_one(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
) -> dict[str, Any]:
    """GET один endpoint. Fail-soft — возвращает skeleton при любой ошибке."""
    url = base_url.rstrip("/") + path
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "path": path}
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "invalid_json", "path": path}
        # Гарантируем dict — некоторые endpoint'ы возвращают list.
        if not isinstance(data, dict):
            return {"ok": True, "data": data, "path": path}
        return data
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": path}


async def _gather_all(
    base_url: str = _DEFAULT_PANEL_BASE,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    app: Any = None,
) -> dict[str, dict[str, Any]]:
    """Fan-out fetch всех _AGGREGATED_ENDPOINTS параллельно.

    Возвращает dict ``{name: raw_payload_or_skeleton}`` — fail-soft, никогда
    не raises. Все таймауты/HTTPError изолированы в ``_fetch_one``.

    Wave 186-fix: если ``app`` передан (FastAPI instance) — используем
    ``httpx.ASGITransport`` для in-process вызовов без loopback. Это
    обходит uvicorn single-worker self-call deadlock, когда handler
    дёргает свой же сервер через 127.0.0.1.
    """
    result: dict[str, dict[str, Any]] = {}
    if app is not None:
        transport = httpx.ASGITransport(app=app)
        client_kwargs: dict[str, Any] = {
            "transport": transport,
            "base_url": "http://owner-panel.local",
            "timeout": timeout,
        }
    else:
        client_kwargs = {"timeout": timeout}
    async with httpx.AsyncClient(**client_kwargs) as client:
        effective_base = "" if app is not None else base_url
        tasks = [_fetch_one(client, effective_base, path) for _, path in _AGGREGATED_ENDPOINTS]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    for (name, path), raw in zip(_AGGREGATED_ENDPOINTS, responses, strict=False):
        if isinstance(raw, Exception):
            result[name] = {"ok": False, "error": str(raw), "path": path}
        elif isinstance(raw, dict):
            result[name] = raw
        else:
            result[name] = {"ok": False, "error": "non_dict", "path": path}
    return result


# ── Traffic-light + summary derivation ──────────────────────────────────────


def _derive_traffic_light(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Из агрегированных raw-данных формирует overall traffic-light + summary.

    Логика (упрощённая, fail-safe — если endpoint недоступен, не понижаем
    светофор, только помечаем компонент как offline):

    - 🔴 red:
        * /api/health → status ∈ {error, critical, unhealthy}
        * network → split_brain == True
        * /api/health недоступен И /api/ecosystem/health недоступен
    - 🟡 yellow:
        * /api/health → status == warning ИЛИ risk_level ∈ {medium, high}
        * cron → overdue_count > 0
        * sentry → quota_used / quota_limit ≥ 0.80
        * pyrogram_disconnects_24h > 0
    - 🟢 green: иначе
    """
    health = raw.get("health") or {}
    network = raw.get("network") or {}
    cron = raw.get("cron") or {}
    sentry = raw.get("sentry") or {}
    ecosystem = raw.get("ecosystem") or {}

    color = "green"
    reasons: list[str] = []

    # — Hard red gates —
    overall_status = str(health.get("status") or "").lower()
    if overall_status in {"error", "critical", "unhealthy"}:
        color = "red"
        reasons.append(f"health.status={overall_status}")

    if bool(network.get("split_brain")):
        color = "red"
        reasons.append("network.split_brain=true")

    health_offline = bool(health.get("error")) and not health.get("status")
    eco_offline = bool(ecosystem.get("error")) and not ecosystem.get("services")
    if health_offline and eco_offline:
        color = "red"
        reasons.append("health+ecosystem unreachable")

    # — Yellow downgrades (не повышаем уже-red) —
    if color != "red":
        if overall_status == "warning":
            color = "yellow"
            reasons.append("health.status=warning")
        risk = str(health.get("risk_level") or "").lower()
        if risk in {"medium", "high"}:
            color = "yellow"
            reasons.append(f"health.risk_level={risk}")

        overdue = int(cron.get("overdue_count") or 0)
        if overdue > 0:
            color = "yellow"
            reasons.append(f"cron.overdue={overdue}")

        # Sentry quota gauge: ≥ 80% → yellow.
        used = int(sentry.get("weekly_quota_used") or 0)
        limit = int(sentry.get("weekly_quota_limit") or 0)
        if limit > 0 and (used / limit) >= 0.80:
            color = "yellow"
            reasons.append(f"sentry.quota={used}/{limit}")

        disconnects = int(network.get("pyrogram_disconnects_24h") or 0)
        if disconnects > 0:
            color = "yellow"
            reasons.append(f"pyrogram_disconnects_24h={disconnects}")

    return {"color": color, "reasons": reasons}


def _system_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает System-card: uptime, dispatcher_tick age, split_brain."""
    health = raw.get("health") or {}
    network = raw.get("network") or {}
    return {
        "krab_uptime_sec": health.get("uptime_sec") or health.get("uptime") or 0,
        "krab_status": health.get("status") or "unknown",
        "risk_level": health.get("risk_level") or "unknown",
        "dispatcher_tick_age_sec": network.get("dispatcher_tick_age_sec")
        or network.get("last_dispatcher_tick_age_sec")
        or 0,
        "split_brain": bool(network.get("split_brain")),
        "pyrogram_disconnects_24h": int(network.get("pyrogram_disconnects_24h") or 0),
        "available": not bool(health.get("error")),
    }


def _ai_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает AI/Models card: active channel, fallback_ready, paid guard.

    Wave 186-fix-2: реальная схема /api/health — AI info живёт в ``chain``
    (active_ai_channel/fallback_ready), а не в ``checks.ai``. Также
    openclaw/local_lm bool флаги дают сигнал о cloud/local чанналах.
    """
    health = raw.get("health") or {}
    checks = health.get("checks") or {}
    chain = health.get("chain") or {}
    # legacy paths (если когда-то появятся)
    ai_block = checks.get("ai") or checks.get("models") or {}
    # active_channel — приоритет chain.active_ai_channel
    active = (
        chain.get("active_ai_channel")
        or ai_block.get("active_channel")
        or ai_block.get("channel")
        or health.get("active_channel")
        or "unknown"
    )
    # fallback_ready — приоритет chain.fallback_ready
    fallback = chain.get("fallback_ready")
    if fallback is None:
        fallback = ai_block.get("fallback_ready", True)
    # available если есть chain или legacy ai_block или известны openclaw/local_lm checks
    has_data = bool(chain) or bool(ai_block) or ("openclaw" in checks) or ("local_lm" in checks)
    return {
        "active_channel": active,
        "fallback_ready": bool(fallback),
        "paid_gemini_blocked_24h": int(ai_block.get("paid_gemini_blocked_24h") or 0),
        "primary_model": ai_block.get("primary_model") or health.get("primary_model") or "",
        "openclaw_healthy": bool(checks.get("openclaw")),
        "local_lm_healthy": bool(checks.get("local_lm")),
        "available": has_data,
    }


def _voice_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает Voice card: gateway alive, ear installed, last STT."""
    voice = raw.get("voice") or {}
    gateway = voice.get("gateway") or {}
    ear = voice.get("ear") or {}
    return {
        "gateway_alive": bool(gateway.get("alive") or gateway.get("ok")),
        "gateway_port": gateway.get("port") or 0,
        "ear_installed": bool(ear.get("installed") or ear.get("ok")),
        "ear_probing": bool(ear.get("probing")),
        "last_stt_ts": voice.get("last_stt_ts") or "",
        "tts_state": voice.get("tts_state") or voice.get("tts") or "unknown",
        "available": not bool(voice.get("error")),
    }


def _memory_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает Memory card: archive size, messages, chunks, last retrieval."""
    mem = raw.get("memory") or {}
    archive = mem.get("archive") or {}
    return {
        "archive_size_mb": float(archive.get("size_mb") or mem.get("size_mb") or 0),
        "messages": int(archive.get("messages") or mem.get("messages") or 0),
        "chunks": int(archive.get("chunks") or mem.get("chunks") or 0),
        "last_retrieval_ts": mem.get("last_retrieval_ts") or "",
        "available": not bool(mem.get("error")),
    }


def _cron_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает Cron card: total agents, overdue, last failures."""
    cron = raw.get("cron") or {}
    agents = cron.get("agents") or cron.get("items") or []
    return {
        "total_agents": int(cron.get("total") or len(agents) or 0),
        "overdue_count": int(cron.get("overdue_count") or 0),
        "failed_recent_count": int(cron.get("failed_recent_count") or 0),
        "available": not bool(cron.get("error")),
    }


def _sentry_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает Sentry card: quota, recent unresolved count."""
    sentry = raw.get("sentry") or {}
    issues = sentry.get("recent_issues") or []
    used = int(sentry.get("weekly_quota_used") or 0)
    limit = int(sentry.get("weekly_quota_limit") or 0)
    pct = int(round(100 * used / limit)) if limit > 0 else 0
    return {
        "quota_used": used,
        "quota_limit": limit,
        "quota_pct": pct,
        "unresolved_count": len(issues) if isinstance(issues, list) else 0,
        "resolved_24h": int(sentry.get("resolved_count_24h") or 0),
        "available": bool(sentry.get("available", True)) and not bool(sentry.get("error")),
    }


def _db_card(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Извлекает DB card: total DBs, total size, integrity warnings."""
    db = raw.get("db") or {}
    items = db.get("databases") or db.get("items") or []
    total_size_mb = 0.0
    integrity_warnings = 0
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            total_size_mb += float(item.get("size_mb") or 0)
            integ = str(item.get("integrity") or "").lower()
            if integ and integ not in {"ok", "clean", "pass"}:
                integrity_warnings += 1
    return {
        "total_dbs": len(items) if isinstance(items, list) else 0,
        "total_size_mb": round(total_size_mb, 2),
        "integrity_warnings": integrity_warnings,
        "available": not bool(db.get("error")),
    }


def _build_dashboard_payload(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Главный аггрегатор: raw → flat dashboard payload."""
    return {
        "ok": True,
        "generated_at": time.time(),
        "traffic_light": _derive_traffic_light(raw),
        "cards": {
            "system": _system_card(raw),
            "ai": _ai_card(raw),
            "voice": _voice_card(raw),
            "memory": _memory_card(raw),
            "cron": _cron_card(raw),
            "sentry": _sentry_card(raw),
            "db": _db_card(raw),
        },
        # Сохраняем raw payload для debug — обёрнут в meta для UI чтобы
        # не нагружать главный JSON парсер. Можно скрыть через ?compact=1.
        "raw_keys": sorted(raw.keys()),
    }


async def _collect_dashboard(
    base_url: str = _DEFAULT_PANEL_BASE,
    app: Any = None,
) -> dict[str, Any]:
    """Public-ish wrapper: cache check → gather → build payload → cache put.

    Wave 186-fix: пробрасывает FastAPI app в _gather_all для ASGITransport.
    """
    cached = _cache_get()
    if cached is not None:
        return cached
    raw = await _gather_all(base_url=base_url, app=app)
    payload = _build_dashboard_payload(raw)
    _cache_put(payload)
    return payload


# ── HTML страница (inline, XSS-safe textContent only) ───────────────────────

_ADMIN_HEALTH_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Krab · Health Dashboard</title>
<style>
  :root {
    --bg: #0c0c0c;
    --card: #161616;
    --border: #2a2a2a;
    --text: #e5e5e5;
    --muted: #8a8a8a;
    --accent: #f97316;
    --danger: #ef4444;
    --warn: #f59e0b;
    --ok: #22c55e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, "SF Pro Text", system-ui, sans-serif;
  }
  header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 24px; }
  header h1 { margin: 0; font-size: 22px; }
  header a { color: var(--muted); text-decoration: none; font-size: 13px; }
  header a:hover { color: var(--accent); }
  .status-line { color: var(--muted); font-size: 12px; margin-left: auto; }

  .traffic-banner {
    background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 20px 24px; margin-bottom: 24px; display: flex; align-items: center; gap: 20px;
  }
  .traffic-banner .light { font-size: 48px; line-height: 1; }
  .traffic-banner .summary { flex: 1; }
  .traffic-banner .summary .title { font-size: 18px; font-weight: 600; margin-bottom: 4px; }
  .traffic-banner .summary .reasons { color: var(--muted); font-size: 13px; }

  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px; }
  .grid.bottom { grid-template-columns: repeat(3, 1fr); }
  .card { background: var(--card); border: 1px solid var(--border); padding: 16px;
          border-radius: 6px; }
  .card h3 { margin: 0 0 12px; font-size: 13px; color: var(--muted);
             text-transform: uppercase; letter-spacing: 0.6px; }
  .card .row { display: flex; justify-content: space-between; padding: 4px 0;
               font-size: 13px; border-bottom: 1px solid rgba(255,255,255,0.04); }
  .card .row:last-child { border-bottom: none; }
  .card .row .k { color: var(--muted); }
  .card .row .v { font-family: "SF Mono", Menlo, monospace; }
  .card.offline { opacity: 0.5; }
  .badge-ok { color: var(--ok); }
  .badge-warn { color: var(--warn); }
  .badge-err { color: var(--danger); }
</style>
</head>
<body>
<header>
  <h1>Health Dashboard</h1>
  <a href="/">Главная</a>
  <a href="/admin/sentry">Sentry</a>
  <a href="/admin/logs">Logs</a>
  <a href="/admin/network">Network</a>
  <a href="/admin/voice">Voice</a>
  <a href="/admin/memory">Memory</a>
  <span class="status-line" id="status">загрузка...</span>
</header>

<div class="traffic-banner">
  <div class="light" id="traffic-light">⚪</div>
  <div class="summary">
    <div class="title" id="traffic-title">проверка...</div>
    <div class="reasons" id="traffic-reasons">—</div>
  </div>
</div>

<div class="grid">
  <div class="card" id="card-system">
    <h3>System</h3>
    <div class="row"><span class="k">uptime</span><span class="v" data-f="system.krab_uptime_sec">-</span></div>
    <div class="row"><span class="k">status</span><span class="v" data-f="system.krab_status">-</span></div>
    <div class="row"><span class="k">risk</span><span class="v" data-f="system.risk_level">-</span></div>
    <div class="row"><span class="k">dispatcher tick</span><span class="v" data-f="system.dispatcher_tick_age_sec">-</span></div>
    <div class="row"><span class="k">split brain</span><span class="v" data-f="system.split_brain">-</span></div>
    <div class="row"><span class="k">disconnects 24h</span><span class="v" data-f="system.pyrogram_disconnects_24h">-</span></div>
  </div>

  <div class="card" id="card-ai">
    <h3>AI / Models</h3>
    <div class="row"><span class="k">channel</span><span class="v" data-f="ai.active_channel">-</span></div>
    <div class="row"><span class="k">primary</span><span class="v" data-f="ai.primary_model">-</span></div>
    <div class="row"><span class="k">fallback ready</span><span class="v" data-f="ai.fallback_ready">-</span></div>
    <div class="row"><span class="k">paid guard blocks 24h</span><span class="v" data-f="ai.paid_gemini_blocked_24h">-</span></div>
  </div>

  <div class="card" id="card-voice">
    <h3>Voice</h3>
    <div class="row"><span class="k">gateway alive</span><span class="v" data-f="voice.gateway_alive">-</span></div>
    <div class="row"><span class="k">gateway port</span><span class="v" data-f="voice.gateway_port">-</span></div>
    <div class="row"><span class="k">ear installed</span><span class="v" data-f="voice.ear_installed">-</span></div>
    <div class="row"><span class="k">ear probing</span><span class="v" data-f="voice.ear_probing">-</span></div>
    <div class="row"><span class="k">last STT</span><span class="v" data-f="voice.last_stt_ts">-</span></div>
    <div class="row"><span class="k">TTS</span><span class="v" data-f="voice.tts_state">-</span></div>
  </div>

  <div class="card" id="card-memory">
    <h3>Memory</h3>
    <div class="row"><span class="k">archive size MB</span><span class="v" data-f="memory.archive_size_mb">-</span></div>
    <div class="row"><span class="k">messages</span><span class="v" data-f="memory.messages">-</span></div>
    <div class="row"><span class="k">chunks</span><span class="v" data-f="memory.chunks">-</span></div>
    <div class="row"><span class="k">last retrieval</span><span class="v" data-f="memory.last_retrieval_ts">-</span></div>
  </div>
</div>

<div class="grid bottom">
  <div class="card" id="card-cron">
    <h3>Cron</h3>
    <div class="row"><span class="k">total agents</span><span class="v" data-f="cron.total_agents">-</span></div>
    <div class="row"><span class="k">overdue</span><span class="v" data-f="cron.overdue_count">-</span></div>
    <div class="row"><span class="k">recent failures</span><span class="v" data-f="cron.failed_recent_count">-</span></div>
  </div>

  <div class="card" id="card-sentry">
    <h3>Sentry</h3>
    <div class="row"><span class="k">quota used</span><span class="v" data-f="sentry.quota_used">-</span></div>
    <div class="row"><span class="k">quota limit</span><span class="v" data-f="sentry.quota_limit">-</span></div>
    <div class="row"><span class="k">quota %</span><span class="v" data-f="sentry.quota_pct">-</span></div>
    <div class="row"><span class="k">unresolved</span><span class="v" data-f="sentry.unresolved_count">-</span></div>
    <div class="row"><span class="k">resolved 24h</span><span class="v" data-f="sentry.resolved_24h">-</span></div>
  </div>

  <div class="card" id="card-db">
    <h3>DB</h3>
    <div class="row"><span class="k">total DBs</span><span class="v" data-f="db.total_dbs">-</span></div>
    <div class="row"><span class="k">total size MB</span><span class="v" data-f="db.total_size_mb">-</span></div>
    <div class="row"><span class="k">integrity warnings</span><span class="v" data-f="db.integrity_warnings">-</span></div>
  </div>
</div>

<script>
  function lightEmoji(color) {
    if (color === 'red') return '🔴';
    if (color === 'yellow') return '🟡';
    if (color === 'green') return '🟢';
    return '⚪';
  }

  function lightTitle(color) {
    if (color === 'red') return 'CRITICAL — требуется внимание';
    if (color === 'yellow') return 'WARNING — есть деградации';
    if (color === 'green') return 'OK — все системы в норме';
    return 'UNKNOWN';
  }

  function fmt(v) {
    if (v === null || v === undefined) return '-';
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (typeof v === 'number') {
      // Большие uptime → дни/часы.
      if (Math.abs(v) > 3600 && Math.abs(v) < 1e10) {
        return v.toFixed(0);
      }
      return String(v);
    }
    return String(v);
  }

  function applyCards(cards) {
    const fields = document.querySelectorAll('[data-f]');
    fields.forEach(function (el) {
      const key = el.getAttribute('data-f');
      const parts = key.split('.');
      let cur = cards;
      for (const p of parts) {
        if (cur && typeof cur === 'object' && p in cur) {
          cur = cur[p];
        } else {
          cur = null;
          break;
        }
      }
      // Use textContent only — XSS-safe.
      el.textContent = fmt(cur);
    });

    // Mark offline cards.
    const cardIds = ['system', 'ai', 'voice', 'memory', 'cron', 'sentry', 'db'];
    for (const cid of cardIds) {
      const block = cards[cid] || {};
      const card = document.getElementById('card-' + cid);
      if (!card) continue;
      if (block.available === false) {
        card.classList.add('offline');
      } else {
        card.classList.remove('offline');
      }
    }
  }

  async function refresh() {
    try {
      const resp = await fetch('/api/admin/health/dashboard');
      const data = await resp.json();
      const tl = data.traffic_light || {};
      const color = tl.color || 'unknown';
      document.getElementById('traffic-light').textContent = lightEmoji(color);
      document.getElementById('traffic-title').textContent = lightTitle(color);
      const reasons = Array.isArray(tl.reasons) ? tl.reasons : [];
      document.getElementById('traffic-reasons').textContent =
        reasons.length ? reasons.join(' · ') : 'нет замечаний';
      applyCards(data.cards || {});
      document.getElementById('status').textContent =
        'обновлено ' + new Date().toLocaleTimeString('ru-RU', { hour12: false });
    } catch (e) {
      document.getElementById('status').textContent = 'ошибка: ' + e.message;
    }
  }

  refresh();
  setInterval(refresh, 10000);
</script>
</body>
</html>
"""


# ── Router factory ──────────────────────────────────────────────────────────


def build_health_dashboard_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/admin/health/dashboard + /admin/health.

    ctx используется только для совместимости с admin builder for-loop
    в ``web_app.py`` (Wave 144+). Endpoint'ы fully read-only, агрегируют
    только публичные endpoint'ы owner-панели — auth не требуется.
    """
    router = APIRouter(tags=["health-dashboard"])

    @router.get("/api/admin/health/dashboard")
    async def health_dashboard() -> JSONResponse:
        """JSON aggregated state. Cached 10s (см. CACHE_TTL_SEC).

        Wave 186-fix: используем ctx.app для ASGITransport in-process вызовов
        (обход uvicorn single-worker self-call deadlock).
        """
        try:
            payload = await _collect_dashboard(app=ctx.app)
        except Exception as exc:  # noqa: BLE001
            logger.warning("health_dashboard_failed", error=str(exc))
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )
        return JSONResponse(payload)

    @router.get("/admin/health", response_class=HTMLResponse)
    async def admin_health_page() -> HTMLResponse:
        """HTML страница с polling 10s + traffic light."""
        return HTMLResponse(
            _ADMIN_HEALTH_HTML,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # Mark ctx as used (для type-checkers и совместимости с admin builder loop).
    _ = ctx
    _ = Path  # imports retained for parity с другими admin routers
    return router
