# -*- coding: utf-8 -*-
"""
Typing indicator admin router — Wave 207 (Session 48).

Owner-side панель для observability метрик «Краб печатает...» (Wave 173
``src/userbot/typing_indicator.py``) и Prometheus-метрик Wave 177
(``src/core/metrics/typing_indicator.py``).

Endpoints (READY):
- GET /api/admin/typing/stats — JSON со снимком метрик + env config.
- GET /admin/typing             — HTML-страница (polling 30s).

Источники данных:
- ``src.core.metrics.typing_indicator`` — Prometheus Counter/Histogram
  объекты. Читаем напрямую через ``.collect()`` (lifetime totals
  с момента старта процесса).
- ``KRAB_TYPING_INDICATOR_ENABLED`` / ``KRAB_TYPING_INDICATOR_BLOCKED_CHATS``
  — env-конфигурация.

Контракт: read-only, никаких write-эндпоинтов. Если prometheus_client
не установлен (slim-env) — возвращаем пустые counters, не падаем.

Note: Prometheus counters cumulative since process boot — «last 24h» /
«last 7d» считаем относительно ``process_uptime_sec`` (нет TSDB локально).
Real time-windowed queries — через Grafana поверх Prometheus scrape.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Боевой стартовый момент процесса (для approximation «uptime»).
_PROCESS_START_TS = time.time()


def _safe_collect_samples(metric: Any) -> list[Any]:
    """Безопасно вытаскивает samples из prometheus_client metric.

    Возвращает [] при любой ошибке (slim-env / metric=None / collect
    raises). Контракт fail-safe — admin-страница не должна падать
    из-за отсутствия prometheus_client.
    """
    if metric is None:
        return []
    try:
        out: list[Any] = []
        for family in metric.collect():
            for sample in family.samples:
                out.append(sample)
        return out
    except Exception as exc:  # noqa: BLE001
        _logger.warning("typing_admin.collect_failed", error=str(exc))
        return []


def _aggregate_counter_by_label(metric: Any, label_name: str) -> dict[str, float]:
    """Суммирует counter по конкретному label.

    Берёт только суффикс ``_total`` (Prometheus exposes ``foo_total``
    и ``foo_created`` — нам нужны только counts).
    """
    out: dict[str, float] = {}
    for s in _safe_collect_samples(metric):
        if not s.name.endswith("_total"):
            continue
        label_value = s.labels.get(label_name)
        if label_value is None:
            continue
        try:
            out[str(label_value)] = out.get(str(label_value), 0.0) + float(s.value)
        except (TypeError, ValueError):
            continue
    return out


def _histogram_snapshot(metric: Any) -> dict[str, Any]:
    """Возвращает snapshot histogram: total count, sum, avg, bucket distribution.

    Бакеты приходят с label ``le`` (upper bound). Среднее = sum / count
    (≈, не учитывает bucket midpoints).
    """
    buckets: list[dict[str, Any]] = []
    total_count: float = 0.0
    total_sum: float = 0.0
    for s in _safe_collect_samples(metric):
        if s.name.endswith("_bucket"):
            le = s.labels.get("le", "")
            try:
                buckets.append({"le": le, "count": float(s.value)})
            except (TypeError, ValueError):
                continue
        elif s.name.endswith("_count"):
            try:
                total_count = float(s.value)
            except (TypeError, ValueError):
                pass
        elif s.name.endswith("_sum"):
            try:
                total_sum = float(s.value)
            except (TypeError, ValueError):
                pass

    avg_sec: float | None = None
    if total_count > 0:
        avg_sec = total_sum / total_count

    return {
        "count": total_count,
        "sum_seconds": total_sum,
        "avg_seconds": avg_sec,
        "buckets": buckets,
    }


def _read_env_config() -> dict[str, Any]:
    """Читает текущую env-конфигурацию typing indicator.

    Дублируем логику ``src.userbot.typing_indicator._is_globally_enabled``
    и ``_blocked_chat_ids`` чтобы не зависеть от приватных функций.
    """
    raw_enabled = os.getenv("KRAB_TYPING_INDICATOR_ENABLED", "1").strip().lower()
    enabled = raw_enabled in {"1", "true", "yes", "on"}

    raw_blocked = os.getenv("KRAB_TYPING_INDICATOR_BLOCKED_CHATS", "")
    blocked = [s.strip() for s in raw_blocked.split(",") if s.strip()]

    return {
        "enabled": enabled,
        "enabled_raw": raw_enabled,
        "blocked_chats": blocked,
        "blocked_count": len(blocked),
    }


def _collect_stats_snapshot() -> dict[str, Any]:
    """Главная функция: собирает полный snapshot для /api/admin/typing/stats.

    Контракт:
      • ``started_by_action`` — суммы Counter по action labels.
      • ``cancelled_by_reason`` — суммы Counter по reason labels.
      • ``duration_histogram`` — histogram snapshot.
      • ``floodwait_by_chat_bucket`` — Counter по chat_id_bucket
        (топ-10 для UI; PII-safe — buckets вместо id).
      • ``floodwait_total`` — общее число FloodWait событий.
      • ``totals`` — derived aggregates (всего стартов, всего успехов и т.п.).
      • ``env`` — KRAB_TYPING_INDICATOR_* config.
      • ``process_uptime_sec`` — секунд с момента старта роутера
        (approximation для «last 24h/7d» — Prometheus counters
        cumulative с момента старта процесса).
    """
    # Импорт через модуль — позволяет тестам patch'ить metric-объекты.
    from src.core.metrics import typing_indicator as _ti  # noqa: PLC0415

    started_by_action = _aggregate_counter_by_label(
        _ti.krab_typing_indicator_started_total, "action"
    )
    cancelled_by_reason = _aggregate_counter_by_label(
        _ti.krab_typing_indicator_cancelled_total, "reason"
    )
    duration_hist = _histogram_snapshot(_ti.krab_typing_indicator_duration_seconds)
    floodwait_by_bucket = _aggregate_counter_by_label(
        _ti.krab_typing_indicator_floodwait_total, "chat_id_bucket"
    )

    # Топ-10 chat_id buckets по FloodWait (сорт по value desc).
    floodwait_top = sorted(
        ({"chat_id_bucket": k, "count": v} for k, v in floodwait_by_bucket.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]
    floodwait_total = sum(floodwait_by_bucket.values())

    totals = {
        "started_total": sum(started_by_action.values()),
        "cancelled_total": sum(cancelled_by_reason.values()),
        "success_total": cancelled_by_reason.get("success", 0.0),
        "error_total": cancelled_by_reason.get("error", 0.0),
        "timeout_total": cancelled_by_reason.get("timeout", 0.0),
        "floodwait_cancel_total": cancelled_by_reason.get("floodwait", 0.0),
        "floodwait_events_total": floodwait_total,
    }

    return {
        "ok": True,
        "started_by_action": started_by_action,
        "cancelled_by_reason": cancelled_by_reason,
        "duration_histogram": duration_hist,
        "floodwait_by_chat_bucket": floodwait_by_bucket,
        "floodwait_top10": floodwait_top,
        "floodwait_total": floodwait_total,
        "totals": totals,
        "env": _read_env_config(),
        "process_uptime_sec": max(0.0, time.time() - _PROCESS_START_TS),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_typing_admin_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с typing-admin endpoints.

    ``ctx`` принят для контракта совместимости (см. cron_admin_router и
    другие admin-роутеры), но не используется — все данные читаются
    из глобальных Prometheus-метрик + env. Read-only роутер,
    write-access проверки не нужны.
    """
    router = APIRouter(tags=["typing-admin"])

    # ── GET /api/admin/typing/stats ─────────────────────────────────────────

    @router.get("/api/admin/typing/stats")
    async def typing_stats() -> dict[str, Any]:
        """Snapshot всех typing-indicator метрик + env config."""
        try:
            return _collect_stats_snapshot()
        except Exception as exc:  # noqa: BLE001
            _logger.error("typing_admin.stats_failed", error=str(exc))
            # Fail-safe response: admin-страница всё-равно сможет
            # отрендерить banner. Не raise HTTPException — UI приятнее
            # обрабатывает 200 + ok=false.
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "env": _read_env_config(),
            }

    # ── GET /admin/typing — HTML page ───────────────────────────────────────

    @router.get("/admin/typing", response_class=HTMLResponse)
    async def typing_admin_page() -> HTMLResponse:
        """HTML страница со снимком typing-метрик (polling 30s)."""
        return HTMLResponse(_TYPING_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/typing ──────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent чтобы избежать
# innerHTML с внешними строками (XSS-safe). Стиль повторяет cron_admin_router.

_TYPING_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Typing Indicator Admin</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
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
        main { padding: 16px 24px; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px 16px;
        }
        .card h2 {
            margin: 0 0 10px 0; font-size: 0.95rem;
            color: var(--text-muted); text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .big-num { font-size: 1.8rem; font-weight: 600; font-family: ui-monospace,
            SFMono-Regular, Menlo, Monaco, monospace; }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.9rem;
        }
        th, td {
            padding: 8px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.75rem;
            letter-spacing: 0.04em;
        }
        tr:last-child td { border-bottom: none; }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        .err-banner { color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 12px; }
        .row { display: flex; justify-content: space-between; align-items: baseline;
            padding: 4px 0; font-size: 0.9rem; }
        .row .label { color: var(--text-muted); }
        .row .value { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        .bar-row { display: flex; align-items: center; gap: 10px; padding: 3px 0;
            font-size: 0.85rem; }
        .bar-row .name { min-width: 130px; color: var(--text-muted); }
        .bar-row .bar-wrap { flex: 1; background: rgba(255,255,255,0.04);
            border-radius: 2px; height: 12px; overflow: hidden; }
        .bar-row .bar-fill { background: var(--accent); height: 100%; }
        .bar-row .num { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco,
            monospace; min-width: 60px; text-align: right; font-size: 0.8rem; }
        .note { color: var(--text-muted); font-size: 0.75rem;
            margin-top: 8px; font-style: italic; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Typing Indicator Metrics</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="err-banner"></div>

        <div class="grid">
            <div class="card">
                <h2>Started — total</h2>
                <div class="big-num" id="card-started">—</div>
                <div class="note">Lifetime since process boot
                    (<span id="card-uptime">—</span>)</div>
            </div>
            <div class="card">
                <h2>Cancelled — success</h2>
                <div class="big-num" id="card-success">—</div>
                <div class="note">Normal exits from typing block</div>
            </div>
            <div class="card">
                <h2>FloodWait events</h2>
                <div class="big-num" id="card-floodwait">—</div>
                <div class="note">Telegram throttled send_chat_action</div>
            </div>
            <div class="card">
                <h2>Average duration</h2>
                <div class="big-num" id="card-avg">—</div>
                <div class="note">Sum / Count (histogram approximation)</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Started by action</h2>
                <div id="started-bars"></div>
            </div>
            <div class="card">
                <h2>Cancelled by reason</h2>
                <div id="cancelled-bars"></div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Duration histogram (buckets)</h2>
                <table>
                    <thead><tr><th>≤ seconds</th><th>cumulative</th></tr></thead>
                    <tbody id="histogram-body"></tbody>
                </table>
            </div>
            <div class="card">
                <h2>Top-10 chat buckets by FloodWait</h2>
                <table>
                    <thead><tr><th>chat_id_bucket</th><th>count</th></tr></thead>
                    <tbody id="floodwait-body"></tbody>
                </table>
                <div class="note">PII-safe: hash(chat_id) % 100</div>
            </div>
        </div>

        <div class="card">
            <h2>Config (env)</h2>
            <div class="row">
                <span class="label">KRAB_TYPING_INDICATOR_ENABLED</span>
                <span class="value" id="env-enabled">—</span>
            </div>
            <div class="row">
                <span class="label">KRAB_TYPING_INDICATOR_BLOCKED_CHATS — size</span>
                <span class="value" id="env-blocklist-size">—</span>
            </div>
            <div class="row">
                <span class="label">Blocklist preview</span>
                <span class="value mono" id="env-blocklist-preview">—</span>
            </div>
        </div>
    </main>
    <script>
        function fmtNum(v) {
            if (v === null || v === undefined) return '—';
            const n = Number(v);
            if (!Number.isFinite(n)) return '—';
            if (n >= 10000) return Math.round(n).toLocaleString('ru-RU');
            if (n >= 1) return Math.round(n).toString();
            return n.toFixed(2);
        }
        function fmtSeconds(v) {
            if (v === null || v === undefined) return '—';
            const n = Number(v);
            if (!Number.isFinite(n)) return '—';
            if (n < 1) return n.toFixed(2) + 's';
            if (n < 60) return n.toFixed(1) + 's';
            return Math.floor(n / 60) + 'm ' + Math.round(n % 60) + 's';
        }
        function fmtUptime(sec) {
            if (!sec || sec < 60) return Math.round(sec || 0) + 's';
            if (sec < 3600) return Math.round(sec / 60) + 'm';
            if (sec < 86400) return Math.round(sec / 3600) + 'h';
            return Math.round(sec / 86400) + 'd';
        }
        function setText(id, text) {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = text;
        }
        function clearChildren(el) {
            while (el && el.firstChild) el.removeChild(el.firstChild);
        }
        function renderBars(containerId, entries) {
            const container = document.getElementById(containerId);
            if (!container) return;
            clearChildren(container);
            if (!entries || entries.length === 0) {
                const p = document.createElement('div');
                p.className = 'note';
                p.textContent = 'нет данных';
                container.appendChild(p);
                return;
            }
            const max = Math.max.apply(null, entries.map(e => e[1])) || 1;
            for (const [name, count] of entries) {
                const row = document.createElement('div');
                row.className = 'bar-row';
                const lbl = document.createElement('span');
                lbl.className = 'name';
                lbl.textContent = name;
                row.appendChild(lbl);
                const barWrap = document.createElement('div');
                barWrap.className = 'bar-wrap';
                const barFill = document.createElement('div');
                barFill.className = 'bar-fill';
                barFill.style.width = Math.min(100, (count / max) * 100) + '%';
                barWrap.appendChild(barFill);
                row.appendChild(barWrap);
                const num = document.createElement('span');
                num.className = 'num';
                num.textContent = fmtNum(count);
                row.appendChild(num);
                container.appendChild(row);
            }
        }
        function renderHistogram(buckets) {
            const body = document.getElementById('histogram-body');
            clearChildren(body);
            if (!buckets || buckets.length === 0) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 2;
                td.className = 'note';
                td.textContent = 'нет данных';
                tr.appendChild(td);
                body.appendChild(tr);
                return;
            }
            for (const b of buckets) {
                const tr = document.createElement('tr');
                const tdLe = document.createElement('td');
                tdLe.className = 'mono';
                tdLe.textContent = b.le;
                const tdCount = document.createElement('td');
                tdCount.className = 'mono';
                tdCount.textContent = fmtNum(b.count);
                tr.appendChild(tdLe);
                tr.appendChild(tdCount);
                body.appendChild(tr);
            }
        }
        function renderFloodwait(rows) {
            const body = document.getElementById('floodwait-body');
            clearChildren(body);
            if (!rows || rows.length === 0) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = 2;
                td.className = 'note';
                td.textContent = 'нет FloodWait событий';
                tr.appendChild(td);
                body.appendChild(tr);
                return;
            }
            for (const r of rows) {
                const tr = document.createElement('tr');
                const tdName = document.createElement('td');
                tdName.className = 'mono';
                tdName.textContent = r.chat_id_bucket;
                const tdCount = document.createElement('td');
                tdCount.className = 'mono';
                tdCount.textContent = fmtNum(r.count);
                tr.appendChild(tdName);
                tr.appendChild(tdCount);
                body.appendChild(tr);
            }
        }
        function renderEnv(env) {
            if (!env) {
                setText('env-enabled', '—');
                setText('env-blocklist-size', '—');
                setText('env-blocklist-preview', '—');
                return;
            }
            setText('env-enabled', env.enabled ? 'ON (' + env.enabled_raw + ')' :
                'OFF (' + env.enabled_raw + ')');
            setText('env-blocklist-size', String(env.blocked_count));
            const preview = (env.blocked_chats || []).slice(0, 5).join(', ') ||
                '(пусто)';
            setText('env-blocklist-preview', preview);
        }
        async function fetchStats() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/typing/stats');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                if (!data.ok) throw new Error(data.error || 'stats_unavailable');

                const totals = data.totals || {};
                setText('card-started', fmtNum(totals.started_total));
                setText('card-success', fmtNum(totals.success_total));
                setText('card-floodwait', fmtNum(totals.floodwait_events_total));

                const hist = data.duration_histogram || {};
                setText('card-avg', fmtSeconds(hist.avg_seconds));
                setText('card-uptime', fmtUptime(data.process_uptime_sec));

                const startedEntries = Object.entries(data.started_by_action || {})
                    .sort((a, b) => b[1] - a[1]);
                renderBars('started-bars', startedEntries);

                const cancelledEntries = Object.entries(data.cancelled_by_reason || {})
                    .sort((a, b) => b[1] - a[1]);
                renderBars('cancelled-bars', cancelledEntries);

                renderHistogram(hist.buckets || []);
                renderFloodwait(data.floodwait_top10 || []);
                renderEnv(data.env);

                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        fetchStats();
        setInterval(fetchStats, 30000);
    </script>
</body>
</html>
"""
