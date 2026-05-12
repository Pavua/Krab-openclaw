# -*- coding: utf-8 -*-
"""
Costs admin router — Wave 155.

Визуальный FinOps дашборд в Owner Panel ``:8080``. Объединяет три источника
учёта расходов в одну живую страницу с polling каждые 30 секунд:

- Wave 78 token-cost (``cost_analytics._calls``) — per-call records по
  моделям с input/output tokens и USD cost.
- Wave 93 budget evaluator (``cost_budget_monitor``) — daily/weekly EUR
  бюджеты с порогами ok/warning/critical (50% / 80% / 100%).
- Wave 120 search cost + Wave 123/138 voice cost — read через Prometheus
  counters (best-effort, fallback на 0.0 если ``prometheus_client`` не
  доступен в slim тестовой среде).

Endpoint'ы:
- GET  /api/admin/costs/dashboard — aggregated JSON для UI.
- GET  /admin/costs                — HTML страница (vanilla JS polling 30s).

Контракт ``/api/admin/costs/dashboard``::

    {
      "ok": true,
      "budget": {
        "daily":  {"used_eur": 1.23, "budget_eur": 5.0, "pct": 24.6,
                   "status": "ok"},
        "weekly": {"used_eur": 4.56, "budget_eur": 25.0, "pct": 18.2,
                   "status": "ok"}
      },
      "breakdown": {
        "24h": [{"provider": "google-vertex", "calls": 42, "cost_usd": 0.12,
                  "input_tokens": 1234, "output_tokens": 567}, ...],
        "7d":  [...]
      },
      "tokens": [{"provider": "...", "input": 1234, "output": 567,
                   "total": 1801}, ...],
      "top_sessions": [{"channel": "telegram:123", "calls": 5,
                         "cost_usd": 0.034}, ...],
      "extras": {
        "search_calls": 12, "search_cost_eur": 0.055,
        "voice_tts_chars": 4321, "voice_tts_cost_eur": 0.0,
        "voice_stt_seconds": 0.0, "voice_stt_cost_eur": 0.0
      },
      "totals": {
        "calls_24h": 42, "calls_7d": 180,
        "cost_24h_usd": 0.12, "cost_7d_usd": 0.83
      }
    }

Все endpoints read-only (GET) — auth check не требуется (дашборд показывает
агрегаты, не secrets). Wire через ``build_costs_admin_router(ctx)`` в
``web_app.py``.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ._context import RouterContext

# Окна для breakdown
_WINDOW_24H_SEC = 24 * 3600
_WINDOW_7D_SEC = 7 * 24 * 3600


def _resolve_provider(model_id: str) -> str:
    """Извлекает provider prefix из model_id (часть до первого ``/``).

    Для голых local id (без slash) возвращает ``lm-studio``.
    Для пустых строк — ``unknown``.
    """
    raw = str(model_id or "").strip()
    if not raw:
        return "unknown"
    if "/" in raw:
        return raw.split("/", 1)[0]
    # Голый id без slash — обычно LM Studio local модель.
    return "lm-studio"


def _aggregate_by_provider(
    calls: list[Any],
    since_ts: float,
) -> list[dict[str, Any]]:
    """Группирует CallRecord по provider за окно since_ts."""
    bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "provider": "",
            "calls": 0,
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    for r in calls:
        ts = getattr(r, "timestamp", None)
        if ts is None or ts < since_ts:
            continue
        provider = _resolve_provider(getattr(r, "model_id", ""))
        entry = bucket[provider]
        entry["provider"] = provider
        entry["calls"] += 1
        entry["cost_usd"] += float(getattr(r, "cost_usd", 0.0) or 0.0)
        entry["input_tokens"] += int(getattr(r, "input_tokens", 0) or 0)
        entry["output_tokens"] += int(getattr(r, "output_tokens", 0) or 0)
    # Сортируем по убыванию стоимости; round для UI.
    rows = sorted(bucket.values(), key=lambda x: -x["cost_usd"])
    return [
        {
            "provider": e["provider"],
            "calls": e["calls"],
            "cost_usd": round(e["cost_usd"], 6),
            "input_tokens": e["input_tokens"],
            "output_tokens": e["output_tokens"],
        }
        for e in rows
    ]


def _top_sessions(calls: list[Any], since_ts: float, limit: int = 5) -> list[dict[str, Any]]:
    """Топ-N сессий (channel) по стоимости за окно."""
    by_channel: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"channel": "", "calls": 0, "cost_usd": 0.0}
    )
    for r in calls:
        ts = getattr(r, "timestamp", None)
        if ts is None or ts < since_ts:
            continue
        ch = str(getattr(r, "channel", "") or "unknown") or "unknown"
        entry = by_channel[ch]
        entry["channel"] = ch
        entry["calls"] += 1
        entry["cost_usd"] += float(getattr(r, "cost_usd", 0.0) or 0.0)
    rows = sorted(by_channel.values(), key=lambda x: -x["cost_usd"])[:limit]
    return [
        {
            "channel": e["channel"],
            "calls": e["calls"],
            "cost_usd": round(e["cost_usd"], 6),
        }
        for e in rows
    ]


def _read_prometheus_counter(metric: Any, **labels: str) -> float:
    """Best-effort чтение значения Prometheus Counter с labels.

    prometheus_client expose internal ``_value`` через ``_value.get()``,
    но интерфейс не публичный. Используем ``labels(...)._value.get()``
    с try/except — если struct поменяется, вернём 0.0.

    В slim тестовой среде где Counter — это _Noop заглушка, ``.labels(...)``
    возвращает self и ``._value`` отсутствует → 0.0.
    """
    if metric is None:
        return 0.0
    try:
        if labels:
            labelled = metric.labels(**labels)
        else:
            labelled = metric
        value_obj = getattr(labelled, "_value", None)
        if value_obj is None:
            return 0.0
        getter = getattr(value_obj, "get", None)
        if getter is None:
            return 0.0
        return float(getter())
    except Exception:  # noqa: BLE001
        return 0.0


def _collect_extras() -> dict[str, float]:
    """Best-effort чтение Prometheus метрик search/voice (Wave 120/123/138).

    Возвращает 0.0 если prometheus_client недоступен или метрика не
    инициализирована (например модуль никогда не импортирован).
    """
    extras = {
        "search_calls": 0.0,
        "search_cost_eur": 0.0,
        "voice_tts_chars": 0.0,
        "voice_tts_cost_eur": 0.0,
        "voice_stt_seconds": 0.0,
        "voice_stt_cost_eur": 0.0,
    }

    # Wave 120: Brave search
    try:
        from ...core.metrics import search as _search_metrics

        extras["search_calls"] = _read_prometheus_counter(
            _search_metrics.krab_search_calls_total, provider="brave", status="ok"
        )
        extras["search_cost_eur"] = _read_prometheus_counter(
            _search_metrics.krab_search_cost_eur_total, provider="brave"
        )
    except Exception:  # noqa: BLE001
        pass

    # Wave 123: voice TTS gateway
    try:
        from ...core.metrics import voice_gateway as _vg_metrics

        extras["voice_tts_chars"] = _read_prometheus_counter(
            getattr(_vg_metrics, "krab_voice_gateway_chars_total", None)
        )
        extras["voice_tts_cost_eur"] = _read_prometheus_counter(
            getattr(_vg_metrics, "krab_voice_gateway_cost_eur_total", None)
        )
    except Exception:  # noqa: BLE001
        pass

    # Wave 138: voice STT
    try:
        from ...core.metrics import voice_stt as _stt_metrics

        extras["voice_stt_seconds"] = _read_prometheus_counter(
            getattr(_stt_metrics, "krab_voice_stt_seconds_total", None)
        )
        extras["voice_stt_cost_eur"] = _read_prometheus_counter(
            getattr(_stt_metrics, "krab_voice_stt_cost_eur_total", None)
        )
    except Exception:  # noqa: BLE001
        pass

    return {k: round(v, 6) for k, v in extras.items()}


def _collect_budget() -> dict[str, dict[str, Any]]:
    """Снимок daily/weekly статуса бюджета (Wave 93).

    Если cost_budget_monitor падает — возвращаем безопасные дефолты.
    """
    try:
        from ...core.cost_budget import cost_budget_monitor as _cbm

        status = _cbm.evaluate_budget_status()
        return {
            "daily": {
                "used_eur": round(status.daily_used_eur, 4),
                "budget_eur": status.daily_budget_eur,
                "pct": round(status.daily_pct, 2),
                "status": status.daily_status,
            },
            "weekly": {
                "used_eur": round(status.weekly_used_eur, 4),
                "budget_eur": status.weekly_budget_eur,
                "pct": round(status.weekly_pct, 2),
                "status": status.weekly_status,
            },
        }
    except Exception:  # noqa: BLE001
        return {
            "daily": {"used_eur": 0.0, "budget_eur": 0.0, "pct": 0.0, "status": "ok"},
            "weekly": {"used_eur": 0.0, "budget_eur": 0.0, "pct": 0.0, "status": "ok"},
        }


def _collect_calls() -> list[Any]:
    """Snapshot ``cost_analytics._calls`` (best-effort)."""
    try:
        from ...core.cost_analytics import cost_analytics as _ca

        return list(_ca._calls)  # noqa: SLF001 — устоявшийся pattern в costs_router
    except Exception:  # noqa: BLE001
        return []


def build_costs_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с costs dashboard UI + aggregated API."""
    router = APIRouter(tags=["costs-admin"])

    # ---------- GET /api/admin/costs/dashboard --------------------------------
    @router.get("/api/admin/costs/dashboard")
    async def costs_dashboard() -> dict[str, Any]:
        """Aggregated FinOps snapshot для /admin/costs UI."""
        now = time.time()
        since_24h = now - _WINDOW_24H_SEC
        since_7d = now - _WINDOW_7D_SEC

        calls = _collect_calls()

        breakdown_24h = _aggregate_by_provider(calls, since_24h)
        breakdown_7d = _aggregate_by_provider(calls, since_7d)
        top_sessions = _top_sessions(calls, since_24h, limit=5)
        budget = _collect_budget()
        extras = _collect_extras()

        # Tokens breakdown (24h окно) — separate легче читается в UI чем
        # вытаскивать поля из breakdown.
        tokens_rows: list[dict[str, Any]] = []
        for row in breakdown_24h:
            tokens_rows.append(
                {
                    "provider": row["provider"],
                    "input": row["input_tokens"],
                    "output": row["output_tokens"],
                    "total": row["input_tokens"] + row["output_tokens"],
                }
            )

        totals = {
            "calls_24h": sum(r["calls"] for r in breakdown_24h),
            "calls_7d": sum(r["calls"] for r in breakdown_7d),
            "cost_24h_usd": round(sum(r["cost_usd"] for r in breakdown_24h), 6),
            "cost_7d_usd": round(sum(r["cost_usd"] for r in breakdown_7d), 6),
        }

        return {
            "ok": True,
            "now": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "budget": budget,
            "breakdown": {"24h": breakdown_24h, "7d": breakdown_7d},
            "tokens": tokens_rows,
            "top_sessions": top_sessions,
            "extras": extras,
            "totals": totals,
        }

    # ---------- GET /admin/costs ----------------------------------------------
    @router.get("/admin/costs", response_class=HTMLResponse)
    async def admin_costs_page() -> HTMLResponse:
        """HTML страница costs dashboard."""
        return HTMLResponse(_COSTS_PAGE_HTML, headers={"Cache-Control": "no-store"})

    # ctx используется для совместимости с factory pattern (как costs_router).
    _ = ctx
    return router


# ── Inline HTML template ────────────────────────────────────────────────────
# Все server-данные рендерятся через .textContent / DOM API без innerHTML —
# защищаемся от XSS (provider/channel могут прийти из user input через chat
# context).

_COSTS_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Krab — Costs (FinOps)</title>
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
  main { padding: 24px; max-width: 1200px; margin: auto; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 8px; padding: 16px; }
  .card-title { color: var(--muted); font-size: 12px; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 8px; }
  .big { font-size: 22px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .pct-bar { height: 8px; background: rgba(139,148,158,0.15); border-radius: 4px;
             margin-top: 10px; overflow: hidden; }
  .pct-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
  .pct-fill.ok { background: var(--ok); }
  .pct-fill.warning { background: var(--warn); }
  .pct-fill.critical { background: var(--err); }
  .status-pill { display: inline-block; padding: 2px 8px; border-radius: 12px;
                 font-size: 11px; font-weight: 600; margin-left: 8px; }
  .s-ok { background: rgba(46,160,67,0.15); color: var(--ok); }
  .s-warning { background: rgba(210,153,34,0.15); color: var(--warn); }
  .s-critical { background: rgba(248,81,73,0.15); color: var(--err); }
  section { margin-top: 24px; }
  section > h2 { font-size: 14px; color: var(--muted); text-transform: uppercase;
                 letter-spacing: 1px; margin: 0 0 12px 0; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 12px; text-align: left;
           border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: normal; font-size: 11px;
       text-transform: uppercase; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .bar-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
  .bar-label { width: 180px; font-size: 12px; color: var(--fg); }
  .bar-track { flex: 1; height: 14px; background: rgba(139,148,158,0.1);
               border-radius: 3px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--accent); border-radius: 3px;
              transition: width 0.3s; }
  .bar-val { width: 100px; text-align: right; font-size: 12px;
             font-variant-numeric: tabular-nums; color: var(--muted); }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  code { font-family: ui-monospace, monospace; font-size: 12px; }
</style>
</head>
<body>
<header>
  <div style="display:flex; align-items:center; gap:18px;">
    <h1>Krab — Costs (FinOps)</h1>
    <nav class="tabs">
      <a href="/admin/models">Models</a>
      <a href="/admin/routing">Routing</a>
      <a href="/admin/ecosystem">Ecosystem</a>
      <a href="/admin/swarm">Swarm</a>
      <a href="/admin/costs" class="active">Costs</a>
      <a href="/admin/inbox">Inbox</a>
    </nav>
  </div>
  <div style="color: var(--muted); font-size: 12px;">
    Refresh: <span id="last-refresh">—</span>
  </div>
</header>
<main>
  <div class="grid">
    <div class="card" id="card-daily">
      <div class="card-title">Daily budget <span id="daily-pill" class="status-pill s-ok">ok</span></div>
      <div class="big"><span id="daily-used">—</span> / <span id="daily-budget">—</span></div>
      <div class="sub"><span id="daily-pct">—</span>%</div>
      <div class="pct-bar"><div id="daily-bar" class="pct-fill ok" style="width:0%"></div></div>
    </div>
    <div class="card" id="card-weekly">
      <div class="card-title">Weekly budget <span id="weekly-pill" class="status-pill s-ok">ok</span></div>
      <div class="big"><span id="weekly-used">—</span> / <span id="weekly-budget">—</span></div>
      <div class="sub"><span id="weekly-pct">—</span>%</div>
      <div class="pct-bar"><div id="weekly-bar" class="pct-fill ok" style="width:0%"></div></div>
    </div>
    <div class="card">
      <div class="card-title">Last 24h</div>
      <div class="big"><span id="cost-24h">—</span></div>
      <div class="sub"><span id="calls-24h">—</span> calls</div>
    </div>
    <div class="card">
      <div class="card-title">Last 7 days</div>
      <div class="big"><span id="cost-7d">—</span></div>
      <div class="sub"><span id="calls-7d">—</span> calls</div>
    </div>
  </div>

  <section>
    <h2>Provider breakdown — last 24h</h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Calls</th>
            <th>Input tokens</th>
            <th>Output tokens</th>
            <th>Cost (USD)</th>
          </tr>
        </thead>
        <tbody id="breakdown-24h"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Provider breakdown — last 7 days</h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Calls</th>
            <th>Input tokens</th>
            <th>Output tokens</th>
            <th>Cost (USD)</th>
          </tr>
        </thead>
        <tbody id="breakdown-7d"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Token consumption — 24h (input+output)</h2>
    <div class="card" id="tokens-chart"></div>
  </section>

  <section>
    <h2>Top 5 costliest sessions — last 24h</h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Channel</th>
            <th>Calls</th>
            <th>Cost (USD)</th>
          </tr>
        </thead>
        <tbody id="top-sessions"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Search &amp; Voice costs (Prometheus)</h2>
    <div class="grid">
      <div class="card">
        <div class="card-title">Brave search</div>
        <div class="big">€<span id="search-cost">—</span></div>
        <div class="sub"><span id="search-calls">—</span> calls</div>
      </div>
      <div class="card">
        <div class="card-title">Voice TTS</div>
        <div class="big">€<span id="tts-cost">—</span></div>
        <div class="sub"><span id="tts-chars">—</span> chars</div>
      </div>
      <div class="card">
        <div class="card-title">Voice STT</div>
        <div class="big">€<span id="stt-cost">—</span></div>
        <div class="sub"><span id="stt-seconds">—</span> sec</div>
      </div>
    </div>
  </section>
</main>
<script>
'use strict';

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
  }
  if (children) {
    for (const c of children) {
      if (c) node.appendChild(c);
    }
  }
  return node;
}

function fmtUSD(v) {
  if (typeof v !== 'number') return '—';
  return '$' + v.toFixed(4);
}

function fmtEUR(v) {
  if (typeof v !== 'number') return '—';
  return v.toFixed(4);
}

function fmtInt(v) {
  if (typeof v !== 'number') return '—';
  return v.toLocaleString();
}

function setBudgetCard(prefix, info) {
  const used = info && typeof info.used_eur === 'number' ? info.used_eur : 0;
  const budget = info && typeof info.budget_eur === 'number' ? info.budget_eur : 0;
  const pct = info && typeof info.pct === 'number' ? info.pct : 0;
  const status = (info && info.status) || 'ok';
  document.getElementById(prefix + '-used').textContent = '€' + used.toFixed(2);
  document.getElementById(prefix + '-budget').textContent = '€' + budget.toFixed(2);
  document.getElementById(prefix + '-pct').textContent = pct.toFixed(1);
  const bar = document.getElementById(prefix + '-bar');
  bar.style.width = Math.min(100, pct).toFixed(1) + '%';
  bar.className = 'pct-fill ' + status;
  const pill = document.getElementById(prefix + '-pill');
  pill.textContent = status;
  pill.className = 'status-pill s-' + status;
}

function renderBreakdownRow(row) {
  const tr = el('tr');
  const code = el('code', { text: row.provider });
  const td0 = el('td');
  td0.appendChild(code);
  tr.appendChild(td0);
  tr.appendChild(el('td', { class: 'num', text: fmtInt(row.calls) }));
  tr.appendChild(el('td', { class: 'num', text: fmtInt(row.input_tokens) }));
  tr.appendChild(el('td', { class: 'num', text: fmtInt(row.output_tokens) }));
  tr.appendChild(el('td', { class: 'num', text: fmtUSD(row.cost_usd) }));
  return tr;
}

function renderBreakdown(tbodyId, rows) {
  const tbody = document.getElementById(tbodyId);
  tbody.textContent = '';
  if (!rows || rows.length === 0) {
    const tr = el('tr');
    tr.appendChild(el('td', { class: 'empty', text: 'No data', colspan: '5' }));
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    tbody.appendChild(renderBreakdownRow(r));
  }
}

function renderTokens(rows) {
  const box = document.getElementById('tokens-chart');
  box.textContent = '';
  if (!rows || rows.length === 0) {
    box.appendChild(el('div', { class: 'empty', text: 'No tokens recorded' }));
    return;
  }
  let max = 0;
  for (const r of rows) {
    if (r.total > max) max = r.total;
  }
  for (const r of rows) {
    const row = el('div', { class: 'bar-row' });
    row.appendChild(el('div', { class: 'bar-label', text: r.provider }));
    const track = el('div', { class: 'bar-track' });
    const pct = max > 0 ? (r.total / max) * 100 : 0;
    const fill = el('div', { class: 'bar-fill' });
    fill.style.width = pct.toFixed(1) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('div', {
      class: 'bar-val',
      text: fmtInt(r.total) + ' (' + fmtInt(r.input) + ' / ' + fmtInt(r.output) + ')',
    }));
    box.appendChild(row);
  }
}

function renderTopSessions(rows) {
  const tbody = document.getElementById('top-sessions');
  tbody.textContent = '';
  if (!rows || rows.length === 0) {
    const tr = el('tr');
    tr.appendChild(el('td', { class: 'empty', text: 'No sessions', colspan: '3' }));
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = el('tr');
    const td0 = el('td');
    td0.appendChild(el('code', { text: r.channel }));
    tr.appendChild(td0);
    tr.appendChild(el('td', { class: 'num', text: fmtInt(r.calls) }));
    tr.appendChild(el('td', { class: 'num', text: fmtUSD(r.cost_usd) }));
    tbody.appendChild(tr);
  }
}

async function refresh() {
  try {
    const resp = await fetch('/api/admin/costs/dashboard');
    const data = await resp.json();
    if (!data.ok) {
      document.getElementById('last-refresh').textContent = 'error';
      return;
    }
    setBudgetCard('daily', data.budget && data.budget.daily);
    setBudgetCard('weekly', data.budget && data.budget.weekly);

    const t = data.totals || {};
    document.getElementById('cost-24h').textContent = fmtUSD(t.cost_24h_usd || 0);
    document.getElementById('calls-24h').textContent = fmtInt(t.calls_24h || 0);
    document.getElementById('cost-7d').textContent = fmtUSD(t.cost_7d_usd || 0);
    document.getElementById('calls-7d').textContent = fmtInt(t.calls_7d || 0);

    const b = data.breakdown || {};
    renderBreakdown('breakdown-24h', b['24h']);
    renderBreakdown('breakdown-7d', b['7d']);
    renderTokens(data.tokens);
    renderTopSessions(data.top_sessions);

    const ex = data.extras || {};
    document.getElementById('search-cost').textContent = fmtEUR(ex.search_cost_eur || 0);
    document.getElementById('search-calls').textContent = fmtInt(ex.search_calls || 0);
    document.getElementById('tts-cost').textContent = fmtEUR(ex.voice_tts_cost_eur || 0);
    document.getElementById('tts-chars').textContent = fmtInt(ex.voice_tts_chars || 0);
    document.getElementById('stt-cost').textContent = fmtEUR(ex.voice_stt_cost_eur || 0);
    document.getElementById('stt-seconds').textContent = fmtInt(ex.voice_stt_seconds || 0);

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
