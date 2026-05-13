# -*- coding: utf-8 -*-
"""
Translator admin router — Wave 216 (Session 49+).

Owner-panel страница ``/admin/translator`` + JSON API для translator
engine — Krab Translator MVP (Wave 32 series).

Показывает state-snapshot всех ключевых компонентов:

- ``translator_session_state.py`` — текущая session_status / active_chats /
  history / stats (persisted в ``data/translator/session_state.json``).
- ``translator_runtime_profile.py`` — language_pair / translation_mode /
  voice_strategy / quick_phrases (persisted в
  ``data/translator/runtime_profile.json``).
- ``translator_engine.py`` — снимок дефолтных constants (модель, лимиты).
- ``translator_finish_gate.py`` — модуль-helpers (без runtime state, только
  presence / version info — поскольку snapshot строится вручную).
- ``translator_live_trial_preflight.py`` — last snapshot из
  ``artifacts/ops/translator_finish_gate_user3_latest.json`` если есть.
- ``translator_mobile_onboarding.py`` — last cached packet из
  ``artifacts/ops/translator_mobile_onboarding_latest.json`` если есть.
- Prometheus metrics с префиксом ``translator_`` если есть.

Endpoints (READY):
- GET /api/admin/translator/state — JSON snapshot всех компонентов.
- GET /admin/translator              — HTML страница.

Безопасность:
- Read-only для v1; write actions (рестарт сессии, переключение profile)
  будут добавлены отдельной волной с ``assert_write_access``.
- Чтение JSON-файлов идёт через helper'ы того же модуля, что и runtime —
  никаких сторонних путей, никакой записи.
- Изначально HTML строит DOM через createElement/textContent — XSS-safe.
- Если runtime state файлов нет — возвращаем skeleton с ``available=False``,
  чтобы фронт не падал и UI оставался информативным.

Match style of ``memory_admin_router.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)


# ── Конфигурация ────────────────────────────────────────────────────────────

# Repo-level persisted translator paths — единый источник истины с
# ``src/userbot/translator_profile.py``.
_DEFAULT_PROFILE_REL = Path("data/translator/runtime_profile.json")
_DEFAULT_SESSION_REL = Path("data/translator/session_state.json")

# Кешированные ops-снапшоты (live trial preflight + mobile onboarding).
_OPS_DIR_REL = Path("artifacts/ops")
_FINISH_GATE_LATEST = "translator_finish_gate_user3_latest.json"
_MOBILE_ONBOARDING_LATEST = "translator_mobile_onboarding_latest.json"

# Лимиты UI.
_HISTORY_PREVIEW_LIMIT = 20
_TIMELINE_PREVIEW_LIMIT = 20

# Размер JSON-snapshot ограничиваем чтобы не выдать 100MB на сломанный файл.
_MAX_FILE_BYTES = 2 * 1024 * 1024


# ── Helpers: file IO ────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Возвращает корень репозитория (../../../ от текущего модуля)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _resolve_path(env_var: str, default_rel: Path) -> Path:
    """Resolve path — env override через ``env_var`` или default относительно repo."""
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return _repo_root() / default_rel


def _read_json_safe(path: Path) -> tuple[bool, Any, str | None]:
    """Best-effort чтение JSON. Возвращает (available, data, error)."""
    if not path.exists():
        return False, None, "file_not_found"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, None, f"stat_failed: {exc}"
    if size > _MAX_FILE_BYTES:
        return False, None, f"file_too_large: {size}"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, None, f"read_failed: {exc}"
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return False, None, f"json_decode_failed: {exc}"
    return True, data, None


# ── Snapshot builders ───────────────────────────────────────────────────────


def _build_session_state_snapshot() -> dict[str, Any]:
    """Снимок ``translator_session_state.json`` + skeleton при отсутствии."""
    path = _resolve_path("KRAB_TRANSLATOR_SESSION_STATE_PATH", _DEFAULT_SESSION_REL)
    available, data, error = _read_json_safe(path)
    snapshot: dict[str, Any] = {
        "path": str(path),
        "available": available,
        "error": error,
    }
    if not available or not isinstance(data, dict):
        # Импортируем default только при отсутствии — экономим cold start.
        try:
            from src.core.translator_session_state import (  # noqa: PLC0415
                default_translator_session_state,
            )

            snapshot["state"] = default_translator_session_state()
            snapshot["from_default"] = True
        except Exception as exc:  # noqa: BLE001
            _logger.debug("translator_admin.default_session_failed", error=str(exc))
            snapshot["state"] = {}
            snapshot["from_default"] = False
        return snapshot

    # history / timeline_preview обрезаем для UI.
    state = dict(data)
    history = state.get("history") or []
    if isinstance(history, list) and len(history) > _HISTORY_PREVIEW_LIMIT:
        state["history"] = history[-_HISTORY_PREVIEW_LIMIT:]
        state["history_truncated"] = True
    timeline = state.get("timeline_preview") or []
    if isinstance(timeline, list) and len(timeline) > _TIMELINE_PREVIEW_LIMIT:
        state["timeline_preview"] = timeline[-_TIMELINE_PREVIEW_LIMIT:]
        state["timeline_truncated"] = True
    snapshot["state"] = state
    snapshot["from_default"] = False
    return snapshot


def _build_runtime_profile_snapshot() -> dict[str, Any]:
    """Снимок ``translator_runtime_profile.json`` + skeleton при отсутствии."""
    path = _resolve_path("KRAB_TRANSLATOR_PROFILE_PATH", _DEFAULT_PROFILE_REL)
    available, data, error = _read_json_safe(path)
    snapshot: dict[str, Any] = {
        "path": str(path),
        "available": available,
        "error": error,
    }
    try:
        from src.core.translator_runtime_profile import (  # noqa: PLC0415
            ALLOWED_LANGUAGE_PAIRS,
            ALLOWED_TRANSLATION_MODES,
            ALLOWED_VOICE_STRATEGIES,
            default_translator_runtime_profile,
        )

        snapshot["allowed"] = {
            "language_pairs": sorted(ALLOWED_LANGUAGE_PAIRS),
            "translation_modes": sorted(ALLOWED_TRANSLATION_MODES),
            "voice_strategies": sorted(ALLOWED_VOICE_STRATEGIES),
        }
        snapshot["defaults"] = dict(default_translator_runtime_profile)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("translator_admin.profile_imports_failed", error=str(exc))
        snapshot["allowed"] = {}
        snapshot["defaults"] = {}

    if available and isinstance(data, dict):
        snapshot["profile"] = data
        snapshot["from_default"] = False
    else:
        snapshot["profile"] = dict(snapshot.get("defaults") or {})
        snapshot["from_default"] = True
    return snapshot


def _build_engine_config_snapshot() -> dict[str, Any]:
    """Снимок constants ``translator_engine``: модель, лимиты, кеш-статус."""
    info: dict[str, Any] = {
        "available": False,
        "preferred_model": "google/gemini-3-flash-preview",
        "max_output_tokens": 512,
        "disable_tools": True,
        "force_cloud": True,
        "cache": None,
    }
    try:
        from src.core import translator_engine as _te  # noqa: PLC0415

        info["available"] = True
        # Грубо извлекаем из source — engine использует hardcoded values в
        # translate_text(). Если будут вынесены в module-level — подберём.
        info["module_path"] = getattr(_te, "__file__", None)
        # Информация о кеше.
        try:
            from src.core.translation_cache import (  # noqa: PLC0415
                translation_cache,
            )

            cache_info: dict[str, Any] = {
                "type": type(translation_cache).__name__,
            }
            for attr in ("size", "max_size", "ttl_seconds", "hits", "misses"):
                if hasattr(translation_cache, attr):
                    try:
                        cache_info[attr] = getattr(translation_cache, attr)
                    except Exception:  # noqa: BLE001
                        pass
            info["cache"] = cache_info
        except Exception as exc:  # noqa: BLE001
            _logger.debug("translator_admin.cache_info_failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("translator_admin.engine_import_failed", error=str(exc))
        info["error"] = str(exc)
    return info


def _build_finish_gate_snapshot() -> dict[str, Any]:
    """Cached finish_gate snapshot из ops артефактов."""
    path = _resolve_path(
        "KRAB_TRANSLATOR_FINISH_GATE_PATH",
        _OPS_DIR_REL / _FINISH_GATE_LATEST,
    )
    available, data, error = _read_json_safe(path)
    snapshot: dict[str, Any] = {
        "path": str(path),
        "available": available,
        "error": error,
    }
    if available and isinstance(data, dict):
        # Сокращаем noisy поля — оставляем только high-level статус.
        snapshot["status"] = data.get("status")
        snapshot["ok"] = bool(data.get("ok"))
        snapshot["generated_at_utc"] = data.get("generated_at_utc")
        snapshot["account"] = data.get("account") or {}
        snapshot["runtime"] = data.get("runtime") or {}
        snapshot["manual_retest"] = data.get("manual_retest") or {}
    return snapshot


def _build_mobile_onboarding_snapshot() -> dict[str, Any]:
    """Cached mobile onboarding packet из ops артефактов."""
    path = _resolve_path(
        "KRAB_TRANSLATOR_MOBILE_ONBOARDING_PATH",
        _OPS_DIR_REL / _MOBILE_ONBOARDING_LATEST,
    )
    available, data, error = _read_json_safe(path)
    snapshot: dict[str, Any] = {
        "path": str(path),
        "available": available,
        "error": error,
    }
    if available and isinstance(data, dict):
        snapshot["status"] = data.get("status")
        snapshot["ready"] = bool(data.get("ready"))
        snapshot["summary"] = data.get("summary") or {}
        snapshot["trial_profiles"] = data.get("trial_profiles") or []
        snapshot["packet_preview"] = data.get("packet_preview") or {}
    return snapshot


def _build_live_trial_preflight_snapshot() -> dict[str, Any]:
    """Heuristic: builder ``translator_live_trial_preflight`` чисто-функциональный,
    runtime snapshot собирается owner-панелью на лету. Здесь возвращаем
    presence / module info — фактический snapshot доступен через
    ``/api/translator/live-trial-preflight``."""
    info: dict[str, Any] = {"available": False}
    try:
        from src.core import (  # noqa: PLC0415
            translator_live_trial_preflight as _ltp,
        )

        info["available"] = True
        info["module_path"] = getattr(_ltp, "__file__", None)
        info["note"] = (
            "Live preflight собирается по запросу через /api/translator/live-trial-preflight"
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def _build_metrics_snapshot() -> dict[str, Any]:
    """Best-effort: собираем prometheus метрики с префиксом ``translator_*``."""
    out: dict[str, Any] = {"available": False, "samples": []}
    try:
        from src.core import prometheus_metrics as _pm  # noqa: PLC0415

        out["available"] = True
        candidates: list[Any] = []
        for attr in dir(_pm):
            if "translator" not in attr.lower() and "translation" not in attr.lower():
                continue
            metric = getattr(_pm, attr, None)
            if metric is None or not hasattr(metric, "collect"):
                continue
            candidates.append((attr, metric))

        for attr, metric in candidates:
            try:
                for family in metric.collect():
                    for s in family.samples:
                        if s.name.endswith("_bucket"):
                            continue
                        out["samples"].append(
                            {
                                "attr": attr,
                                "metric": s.name,
                                "labels": dict(s.labels) if s.labels else {},
                                "value": float(s.value),
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                _logger.debug(
                    "translator_admin.metric_collect_failed",
                    metric=attr,
                    error=str(exc),
                )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def _build_full_snapshot_sync() -> dict[str, Any]:
    """Полный sync-snapshot — оборачиваем в ``asyncio.to_thread`` в endpoint'е."""
    return {
        "ok": True,
        "session": _build_session_state_snapshot(),
        "profile": _build_runtime_profile_snapshot(),
        "engine": _build_engine_config_snapshot(),
        "finish_gate": _build_finish_gate_snapshot(),
        "mobile_onboarding": _build_mobile_onboarding_snapshot(),
        "live_trial_preflight": _build_live_trial_preflight_snapshot(),
        "metrics": _build_metrics_snapshot(),
    }


# ── Factory ─────────────────────────────────────────────────────────────────


def build_translator_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter для /admin/translator + /api/admin/translator/state."""
    _ = ctx  # read-only v1 — write actions добавятся отдельной волной.

    router = APIRouter(tags=["translator-admin"])

    # ── GET /api/admin/translator/state ─────────────────────────────────────

    @router.get("/api/admin/translator/state")
    async def translator_state() -> dict[str, Any]:
        """JSON snapshot всех translator-компонентов."""
        try:
            snapshot = await asyncio.to_thread(_build_full_snapshot_sync)
        except Exception as exc:  # noqa: BLE001
            _logger.error("translator_admin.state_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"translator_state_failed: {exc}",
            ) from exc
        return snapshot

    # ── GET /admin/translator — HTML page ───────────────────────────────────

    @router.get("/admin/translator", response_class=HTMLResponse)
    async def translator_admin_page() -> HTMLResponse:
        """HTML страница со state snapshot translator engine."""
        return HTMLResponse(_TRANSLATOR_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/translator ─────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — XSS-safe
# (никакого innerHTML с внешними строками).

_TRANSLATOR_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Translator Admin</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #a78bfa;
            --ok: #22c55e;
            --warn: #facc15;
            --err: #ef4444;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.4;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 24px;
            background: #000; border-bottom: 1px solid var(--border);
        }
        header h1 { margin: 0; font-size: 1.4rem; }
        header .meta { color: var(--text-muted); font-size: 0.85rem; }
        main { padding: 16px 24px; max-width: 1200px; }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px;
        }
        .card .label {
            color: var(--text-muted);
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .card .value {
            font-size: 1.2rem;
            font-weight: 600;
            margin-top: 4px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            word-break: break-word;
        }
        .card .sub {
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.85rem;
            margin-bottom: 24px;
        }
        th, td {
            padding: 6px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.72rem;
            letter-spacing: 0.04em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(167, 139, 250, 0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        .section-title {
            font-size: 1.1rem;
            margin: 28px 0 10px 0;
            color: var(--accent);
        }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner {
            color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08);
            border-radius: 4px; margin-bottom: 12px;
        }
        pre.json {
            background: #0a0a0a;
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 10px 12px;
            font-size: 0.8rem;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-word;
            color: var(--text);
            max-height: 400px;
        }
        button {
            background: rgba(167,139,250,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 6px 14px;
            font-size: 0.85rem;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }
        button:hover { background: rgba(167,139,250,0.2); }
    </style>
</head>
<body>
    <header>
        <h1>🌐 Krab · Translator Admin</h1>
        <div class="meta">
            <button id="refresh-btn" type="button">↻ Refresh</button>
            <span id="last-update">—</span>
        </div>
    </header>
    <main>
        <div id="err-banner"></div>

        <div class="section-title">📞 Session</div>
        <div id="session-cards" class="cards"></div>

        <div class="section-title">⚙️ Runtime Profile</div>
        <div id="profile-cards" class="cards"></div>

        <div class="section-title">🧠 Engine Config</div>
        <div id="engine-cards" class="cards"></div>

        <div class="section-title">📋 Translation history (последние)</div>
        <table id="history-table">
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Lang</th>
                    <th>Original</th>
                    <th>Translation</th>
                    <th>ms</th>
                </tr>
            </thead>
            <tbody id="history-body"></tbody>
        </table>

        <div class="section-title">📱 Mobile onboarding (cached)</div>
        <div id="mobile-cards" class="cards"></div>

        <div class="section-title">🏁 Finish gate (cached)</div>
        <div id="gate-cards" class="cards"></div>

        <div class="section-title">📈 Translator metrics</div>
        <table id="metrics-table">
            <thead>
                <tr>
                    <th>Attr</th>
                    <th>Metric</th>
                    <th>Labels</th>
                    <th>Value</th>
                </tr>
            </thead>
            <tbody id="metrics-body"></tbody>
        </table>

        <div class="section-title">📄 Raw snapshot</div>
        <pre id="raw-json" class="json mono">—</pre>
    </main>
    <script>
        function _td(text, mono) {
            const td = document.createElement('td');
            if (mono) td.className = 'mono';
            td.textContent = text;
            return td;
        }
        function mkCard(label, value, sub) {
            const div = document.createElement('div');
            div.className = 'card';
            const l = document.createElement('div');
            l.className = 'label';
            l.textContent = label;
            div.appendChild(l);
            const v = document.createElement('div');
            v.className = 'value';
            v.textContent = (value === null || value === undefined || value === '') ? '—' : String(value);
            div.appendChild(v);
            if (sub !== undefined && sub !== null) {
                const s = document.createElement('div');
                s.className = 'sub';
                s.textContent = String(sub);
                div.appendChild(s);
            }
            return div;
        }
        function setErr(msg) {
            const banner = document.getElementById('err-banner');
            while (banner.firstChild) banner.removeChild(banner.firstChild);
            if (msg) {
                const div = document.createElement('div');
                div.className = 'err-banner';
                div.textContent = 'Ошибка: ' + msg;
                banner.appendChild(div);
            }
        }
        function fmtBool(v) { return v ? 'yes' : 'no'; }
        function clearChildren(el) {
            while (el.firstChild) el.removeChild(el.firstChild);
        }
        function renderSession(payload) {
            const cards = document.getElementById('session-cards');
            clearChildren(cards);
            const state = (payload && payload.state) || {};
            const avail = payload && payload.available;
            cards.appendChild(mkCard('Status', state.session_status || '—',
                payload.from_default ? 'default (no file)' : 'persisted'));
            cards.appendChild(mkCard('Session id', state.session_id || '—', state.active_session_label || ''));
            cards.appendChild(mkCard('Muted', fmtBool(state.translation_muted), 'translation_muted'));
            const chats = Array.isArray(state.active_chats) ? state.active_chats : [];
            cards.appendChild(mkCard('Active chats', String(chats.length),
                chats.length ? chats.slice(0, 3).join(', ') : 'none'));
            cards.appendChild(mkCard('Last pair', state.last_language_pair || '—', 'last_language_pair'));
            const stats = state.stats || {};
            cards.appendChild(mkCard('Translations',
                String(stats.total_translations || 0),
                'total_latency_ms=' + (stats.total_latency_ms || 0)));
            cards.appendChild(mkCard('Last event', state.last_event || '—', state.updated_at || ''));
            cards.appendChild(mkCard('File',
                avail ? 'OK' : 'missing',
                payload.path || ''));
        }
        function renderProfile(payload) {
            const cards = document.getElementById('profile-cards');
            clearChildren(cards);
            const prof = (payload && payload.profile) || {};
            cards.appendChild(mkCard('Lang pair', prof.language_pair || '—',
                payload.from_default ? 'default' : 'persisted'));
            cards.appendChild(mkCard('Mode', prof.translation_mode || '—', 'translation_mode'));
            cards.appendChild(mkCard('Voice', prof.voice_strategy || '—', 'voice_strategy'));
            cards.appendChild(mkCard('Target', prof.target_device || '—', 'target_device'));
            cards.appendChild(mkCard('Ordinary calls', fmtBool(prof.ordinary_calls_enabled), ''));
            cards.appendChild(mkCard('Internet calls', fmtBool(prof.internet_calls_enabled), ''));
            cards.appendChild(mkCard('Subtitles', fmtBool(prof.subtitles_enabled), ''));
            cards.appendChild(mkCard('Timeline', fmtBool(prof.timeline_enabled), ''));
            cards.appendChild(mkCard('Diagnostics', fmtBool(prof.diagnostics_enabled), ''));
            const qp = Array.isArray(prof.quick_phrases) ? prof.quick_phrases : [];
            cards.appendChild(mkCard('Quick phrases', String(qp.length), qp.slice(0, 2).join(' / ')));
        }
        function renderEngine(payload) {
            const cards = document.getElementById('engine-cards');
            clearChildren(cards);
            cards.appendChild(mkCard('Module', payload.available ? 'loaded' : 'missing', 'translator_engine'));
            cards.appendChild(mkCard('Preferred model', payload.preferred_model || '—', 'flash tier'));
            cards.appendChild(mkCard('Max tokens', String(payload.max_output_tokens || '—'), 'max_output_tokens'));
            cards.appendChild(mkCard('Disable tools', fmtBool(payload.disable_tools), ''));
            cards.appendChild(mkCard('Force cloud', fmtBool(payload.force_cloud), ''));
            const cache = payload.cache || {};
            cards.appendChild(mkCard('Cache',
                cache.type || '—',
                'size=' + (cache.size === undefined ? '?' : cache.size) +
                ' hits=' + (cache.hits === undefined ? '?' : cache.hits) +
                ' misses=' + (cache.misses === undefined ? '?' : cache.misses)));
        }
        function renderHistory(payload) {
            const body = document.getElementById('history-body');
            clearChildren(body);
            const state = (payload && payload.state) || {};
            const history = Array.isArray(state.history) ? state.history : [];
            if (!history.length) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 5;
                td.textContent = '— нет переводов в истории';
                td.style.color = 'var(--text-muted)';
                tr.appendChild(td);
                body.appendChild(tr);
                return;
            }
            for (const h of history.slice().reverse()) {
                const tr = document.createElement('tr');
                tr.appendChild(_td(String(h.timestamp || '—'), true));
                tr.appendChild(_td((h.src_lang || '?') + '→' + (h.tgt_lang || '?'), true));
                tr.appendChild(_td(String(h.original || '')));
                tr.appendChild(_td(String(h.translation || '')));
                tr.appendChild(_td(String(h.latency_ms || 0), true));
                body.appendChild(tr);
            }
        }
        function renderMobile(payload) {
            const cards = document.getElementById('mobile-cards');
            clearChildren(cards);
            if (!payload || !payload.available) {
                cards.appendChild(mkCard('Status', 'unavailable', payload && payload.error ? payload.error : 'no cached file'));
                return;
            }
            cards.appendChild(mkCard('Status', payload.status || '—', 'mobile_onboarding'));
            cards.appendChild(mkCard('Ready', fmtBool(payload.ready), ''));
            const summary = payload.summary || {};
            cards.appendChild(mkCard('Mobile state', summary.mobile_status || '—', ''));
            cards.appendChild(mkCard('Registered devices', String(summary.registered_devices || 0), ''));
            cards.appendChild(mkCard('Bound devices', String(summary.bound_devices || 0), ''));
            cards.appendChild(mkCard('Selected device', summary.selected_device_id || '—', ''));
            const preview = payload.packet_preview || {};
            cards.appendChild(mkCard('Recommended profile',
                preview.recommended_trial_profile || '—', preview.next_step || ''));
        }
        function renderGate(payload) {
            const cards = document.getElementById('gate-cards');
            clearChildren(cards);
            if (!payload || !payload.available) {
                cards.appendChild(mkCard('Status', 'unavailable',
                    payload && payload.error ? payload.error : 'no cached file'));
                return;
            }
            cards.appendChild(mkCard('Status', payload.status || '—', 'finish_gate'));
            cards.appendChild(mkCard('OK', fmtBool(payload.ok), ''));
            const runtime = payload.runtime || {};
            cards.appendChild(mkCard('Route model', runtime.current_route_model || '—',
                runtime.current_route_channel || ''));
            cards.appendChild(mkCard('Voice gateway',
                fmtBool(runtime.voice_gateway_configured), ''));
            const retest = payload.manual_retest || {};
            cards.appendChild(mkCard('Manual retest', retest.status || '—', retest.next_step || ''));
            cards.appendChild(mkCard('Generated at', payload.generated_at_utc || '—', ''));
        }
        function renderMetrics(payload) {
            const body = document.getElementById('metrics-body');
            clearChildren(body);
            const samples = (payload && payload.samples) || [];
            if (!samples.length) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 4;
                td.textContent = '— нет translator_* метрик (пока)';
                td.style.color = 'var(--text-muted)';
                tr.appendChild(td);
                body.appendChild(tr);
                return;
            }
            for (const s of samples.slice(0, 50)) {
                const tr = document.createElement('tr');
                tr.appendChild(_td(String(s.attr || '—'), true));
                tr.appendChild(_td(String(s.metric || '—'), true));
                let labelStr = '';
                try {
                    labelStr = Object.keys(s.labels || {})
                        .map(k => k + '=' + s.labels[k])
                        .join(' ');
                } catch (e) { labelStr = ''; }
                tr.appendChild(_td(labelStr, true));
                tr.appendChild(_td(String(s.value), true));
                body.appendChild(tr);
            }
        }
        async function refresh() {
            try {
                const res = await fetch('/api/admin/translator/state');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                renderSession(data.session || {});
                renderProfile(data.profile || {});
                renderEngine(data.engine || {});
                renderHistory(data.session || {});
                renderMobile(data.mobile_onboarding || {});
                renderGate(data.finish_gate || {});
                renderMetrics(data.metrics || {});
                document.getElementById('raw-json').textContent =
                    JSON.stringify(data, null, 2);
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
                setErr('');
            } catch (e) {
                setErr('state: ' + e.message);
            }
        }
        document.getElementById('refresh-btn').addEventListener('click', refresh);
        refresh();
        setInterval(refresh, 60000);
    </script>
</body>
</html>
"""
