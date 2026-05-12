# -*- coding: utf-8 -*-
"""
Inbox admin router — Wave 157.

Визуальная триаж-страница inbox в Owner Panel ``:8080``. Тонкий аггрегатор
поверх существующего ``inbox_router`` (Wave 3 + Wave O), который уже
содержит read/write контракт для inbox items, bulk-ack stale, cleanup
stale-open и др.

Endpoint'ы:
- GET  /api/admin/inbox/dashboard — aggregated JSON для UI:
    quick_stats (open/stale/acked/done/attention), kinds breakdown,
    items list по выбранному фильтру (status/kind/limit).
- GET  /admin/inbox                — HTML страница (vanilla JS polling 20s).
  Поддерживает фильтры (status/kind), показывает таблицу items с действиями
  (ack single, ack all >12h, cleanup stale-open, archive done).

Контракт ``/api/admin/inbox/dashboard``::

    {
      "ok": true,
      "now": "2026-05-12T20:00:00+00:00",
      "stats": {
        "total_open": 42,
        "stale_open": 5,
        "acked": 12,
        "done": 100,
        "cancelled": 3,
        "attention": 4
      },
      "kinds": [
        {"kind": "owner_request", "open": 5, "acked": 1},
        {"kind": "approval_request", "open": 2, "acked": 0},
        ...
      ],
      "items": [
        {"item_id": "...", "kind": "owner_request", "status": "open",
         "severity": "warning", "title": "...", "created_at_utc": "...",
         "age_hours": 14.2, "actions": ["ack","done","cancel"]},
        ...
      ]
    }

Read endpoint без auth (как costs_admin) — показывает агрегаты. Write
действия по существующим POST-ам в ``inbox_router`` (UI делает fetch
с X-Krab-Web-Key header / token query).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from ._context import RouterContext

# Лимит возвращаемого items списка (UI пейджинг отсутствует — единая страница).
_DEFAULT_ITEM_LIMIT = 50
_MAX_ITEM_LIMIT = 200


def _resolve_inbox_service() -> Any:
    """Lazy import singleton — позволяет тестам патчить через ``patch``."""
    from ...core.inbox_service import inbox_service

    return inbox_service


def _compute_age_hours(created_at_iso: str) -> float:
    """Возвращает возраст в часах (best-effort, 0.0 при parse error)."""
    raw = str(created_at_iso or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    return round(delta.total_seconds() / 3600.0, 2)


def _annotate_item(item: dict[str, Any]) -> dict[str, Any]:
    """Добавляет computed-поля (age_hours, actions) к raw inbox item dict."""
    annotated = dict(item)
    annotated["age_hours"] = _compute_age_hours(str(item.get("created_at_utc") or ""))
    # Stale = open + старше 12 часов (соответствует bulk-ack-stale default).
    status = str(item.get("status") or "").strip().lower()
    annotated["is_stale"] = status == "open" and annotated["age_hours"] > 12.0
    # Список возможных действий (UI рисует кнопки по списку).
    if status == "open":
        annotated["actions"] = ["ack", "done", "cancel"]
    elif status == "acked":
        annotated["actions"] = ["done", "cancel"]
    else:
        annotated["actions"] = []
    return annotated


def _collect_stats(svc: Any) -> dict[str, Any]:
    """Снимок счётчиков для quick stats cards.

    Использует существующий ``get_workflow_snapshot().summary`` чтобы не
    дублировать логику и не пересчитывать вручную.
    """
    try:
        snapshot = svc.get_workflow_snapshot()
        summary = dict(snapshot.get("summary") or {})
    except Exception:  # noqa: BLE001 — graceful UI не должен падать
        summary = {}

    # Подсчёт stale open отдельным методом — не присутствует в summary.
    try:
        stale_open = svc.list_stale_open_items(kind="", limit=200)
        stale_count = len(stale_open)
    except Exception:  # noqa: BLE001
        stale_count = 0

    # Attention = открытые items с severity warning/error.
    try:
        open_items = svc.list_items(status="open", limit=200)
        attention_count = sum(
            1 for it in open_items if str(it.get("severity") or "").lower() in {"warning", "error"}
        )
        total_open = len(open_items)
    except Exception:  # noqa: BLE001
        attention_count = 0
        total_open = int(summary.get("open") or 0)

    return {
        "total_open": int(summary.get("open") or total_open),
        "stale_open": stale_count,
        "acked": int(summary.get("acked") or 0),
        "done": int(summary.get("done") or 0),
        "cancelled": int(summary.get("cancelled") or 0),
        "attention": attention_count,
    }


def _kinds_breakdown(svc: Any) -> list[dict[str, Any]]:
    """Группировка по kind: open и acked counts.

    UI показывает таблицу "сколько каких kind висит" — даёт оператору
    понимание где скапливаются items (proactive_action vs owner_request).
    """
    try:
        all_items = svc.list_items(status="all", limit=500)
    except TypeError:
        # Старая сигнатура: list_items(status="open"...) — ok fallback.
        try:
            all_items = svc.list_items(limit=500)
        except Exception:  # noqa: BLE001
            return []
    except Exception:  # noqa: BLE001
        return []

    bucket: dict[str, dict[str, int]] = {}
    for it in all_items:
        kind = str(it.get("kind") or "unknown")
        status = str(it.get("status") or "").lower()
        slot = bucket.setdefault(kind, {"open": 0, "acked": 0, "done": 0})
        if status == "open":
            slot["open"] += 1
        elif status == "acked":
            slot["acked"] += 1
        elif status == "done":
            slot["done"] += 1
    rows = [{"kind": k, **counts} for k, counts in bucket.items()]
    # Сортировка: сначала те, у кого больше open, чтобы триаж шёл сверху вниз.
    rows.sort(key=lambda r: (-r["open"], -r["acked"], r["kind"]))
    return rows


def build_inbox_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с inbox admin dashboard UI + JSON aggregator."""
    router = APIRouter(tags=["inbox-admin"])

    # ---------- GET /api/admin/inbox/dashboard --------------------------------
    @router.get("/api/admin/inbox/dashboard")
    async def inbox_admin_dashboard(
        status: str = Query(default="open"),
        kind: str = Query(default=""),
        limit: int = Query(default=_DEFAULT_ITEM_LIMIT, ge=1, le=_MAX_ITEM_LIMIT),
    ) -> dict[str, Any]:
        """Aggregated snapshot для /admin/inbox UI (stats + kinds + items)."""
        svc = _resolve_inbox_service()
        try:
            raw_items = svc.list_items(status=status, kind=kind, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"inbox_list_failed: {exc!s}") from exc

        items = [_annotate_item(it) for it in raw_items]
        stats = _collect_stats(svc)
        kinds = _kinds_breakdown(svc)

        return {
            "ok": True,
            "now": datetime.now(timezone.utc).isoformat(),
            "filter": {"status": status, "kind": kind, "limit": limit},
            "stats": stats,
            "kinds": kinds,
            "items": items,
        }

    # ---------- GET /admin/inbox ----------------------------------------------
    @router.get("/admin/inbox", response_class=HTMLResponse)
    async def admin_inbox_page() -> HTMLResponse:
        """HTML страница inbox triage."""
        return HTMLResponse(_INBOX_PAGE_HTML, headers={"Cache-Control": "no-store"})

    # ctx используется для совместимости с factory pattern.
    _ = ctx
    return router


# ── Inline HTML template ────────────────────────────────────────────────────
# Server-данные рендерятся через .textContent / DOM API без innerHTML —
# защищаемся от XSS (title/body/source items могут прийти из user input).

_INBOX_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Krab — Inbox (Triage)</title>
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
  main { padding: 24px; max-width: 1400px; margin: auto; }
  .grid { display: grid; gap: 16px;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--border);
          border-radius: 8px; padding: 16px; }
  .card-title { color: var(--muted); font-size: 12px; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 8px; }
  .big { font-size: 22px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; margin-top: 4px; }
  section { margin-top: 24px; }
  section > h2 { font-size: 14px; color: var(--muted); text-transform: uppercase;
                 letter-spacing: 1px; margin: 0 0 12px 0; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 12px; text-align: left;
           border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: normal; font-size: 11px;
       text-transform: uppercase; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 10px;
          font-size: 11px; font-weight: 600; }
  .p-open { background: rgba(88,166,255,0.15); color: var(--accent); }
  .p-acked { background: rgba(210,153,34,0.15); color: var(--warn); }
  .p-done { background: rgba(46,160,67,0.15); color: var(--ok); }
  .p-cancelled { background: rgba(139,148,158,0.15); color: var(--muted); }
  .sev-info { color: var(--muted); }
  .sev-warning { color: var(--warn); }
  .sev-error { color: var(--err); }
  .stale-row { background: rgba(248,81,73,0.05); }
  .filter-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
                margin-bottom: 16px; padding: 12px; background: var(--card);
                border: 1px solid var(--border); border-radius: 6px; }
  .filter-bar label { font-size: 12px; color: var(--muted); margin-right: 4px; }
  .filter-bar select, .filter-bar input {
    background: var(--bg); color: var(--fg); border: 1px solid var(--border);
    padding: 5px 8px; border-radius: 4px; font-size: 13px;
  }
  button { background: var(--card); color: var(--fg); border: 1px solid var(--border);
           padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 12px;
           margin-right: 4px; }
  button:hover { border-color: var(--accent); color: var(--accent); }
  button.primary { background: rgba(88,166,255,0.1); border-color: var(--accent);
                   color: var(--accent); }
  button.danger { background: rgba(248,81,73,0.1); border-color: var(--err);
                  color: var(--err); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .bulk-bar { padding: 12px; background: var(--card); border: 1px solid var(--border);
              border-radius: 6px; margin-bottom: 16px; }
  .bulk-bar h3 { margin: 0 0 8px 0; font-size: 13px; color: var(--muted);
                 text-transform: uppercase; letter-spacing: 1px; }
  .bulk-bar p { margin: 6px 0; font-size: 12px; color: var(--muted); }
  .empty { color: var(--muted); font-style: italic; padding: 12px 0; }
  .toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 16px;
           background: var(--card); border: 1px solid var(--border);
           border-radius: 6px; font-size: 13px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
           max-width: 360px; }
  .toast.ok { border-color: var(--ok); color: var(--ok); }
  .toast.err { border-color: var(--err); color: var(--err); }
  code { font-family: ui-monospace, monospace; font-size: 12px; }
  .age-fresh { color: var(--ok); }
  .age-warn { color: var(--warn); }
  .age-stale { color: var(--err); }
</style>
</head>
<body>
<header>
  <div style="display:flex; align-items:center; gap:18px;">
    <h1>Krab — Inbox (Triage)</h1>
    <nav class="tabs">
      <a href="/admin/models">Models</a>
      <a href="/admin/routing">Routing</a>
      <a href="/admin/ecosystem">Ecosystem</a>
      <a href="/admin/swarm">Swarm</a>
      <a href="/admin/costs">Costs</a>
      <a href="/admin/inbox" class="active">Inbox</a>
    </nav>
  </div>
  <div style="color: var(--muted); font-size: 12px;">
    Refresh: <span id="last-refresh">&mdash;</span>
  </div>
</header>
<main>
  <div class="grid">
    <div class="card">
      <div class="card-title">Open</div>
      <div class="big" id="stat-open">&mdash;</div>
      <div class="sub"><span id="stat-attention">&mdash;</span> attention</div>
    </div>
    <div class="card">
      <div class="card-title">Stale (&gt;12h)</div>
      <div class="big sev-error" id="stat-stale">&mdash;</div>
      <div class="sub">need triage</div>
    </div>
    <div class="card">
      <div class="card-title">Acked</div>
      <div class="big sev-warning" id="stat-acked">&mdash;</div>
      <div class="sub">processing</div>
    </div>
    <div class="card">
      <div class="card-title">Done</div>
      <div class="big sev-info" id="stat-done">&mdash;</div>
      <div class="sub">completed</div>
    </div>
    <div class="card">
      <div class="card-title">Cancelled</div>
      <div class="big sev-info" id="stat-cancelled">&mdash;</div>
      <div class="sub">archived</div>
    </div>
  </div>

  <section>
    <h2>Bulk actions</h2>
    <div class="bulk-bar">
      <h3>Stale-open cleanup</h3>
      <p>Закрывает open items старше 12 часов (POST /api/inbox/bulk-ack-stale).
         Web-key читается из URL ?token=... либо localStorage.</p>
      <button class="primary" id="btn-ack-stale">Ack stale (&gt;12h)</button>
      <button class="danger" id="btn-cleanup-stale">Cleanup &gt;7d (archive)</button>
      <button id="btn-cancel-low">Cancel info-severity (&gt;12h)</button>
    </div>
  </section>

  <section>
    <h2>Filters</h2>
    <div class="filter-bar">
      <label>Status:</label>
      <select id="f-status">
        <option value="open">open</option>
        <option value="acked">acked</option>
        <option value="done">done</option>
        <option value="cancelled">cancelled</option>
        <option value="all">all</option>
      </select>
      <label>Kind:</label>
      <input type="text" id="f-kind" placeholder="any" style="width: 180px;">
      <label>Limit:</label>
      <input type="number" id="f-limit" value="50" min="1" max="200"
             style="width: 70px;">
      <button class="primary" id="btn-apply-filter">Apply</button>
    </div>
  </section>

  <section>
    <h2>Kinds breakdown</h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Kind</th>
            <th class="num">Open</th>
            <th class="num">Acked</th>
            <th class="num">Done</th>
          </tr>
        </thead>
        <tbody id="kinds-tbody"></tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Items <span style="color: var(--muted); font-size: 12px;">
      (<span id="items-count">&mdash;</span>)</span></h2>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Kind</th>
            <th>Sev</th>
            <th>Status</th>
            <th>Title</th>
            <th class="num">Age (h)</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="items-tbody"></tbody>
      </table>
    </div>
  </section>
</main>
<div id="toast-host"></div>
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

function fmtInt(v) {
  if (typeof v !== 'number') return '0';
  return v.toLocaleString();
}

function toast(msg, kind) {
  const host = document.getElementById('toast-host');
  const t = el('div', { class: 'toast ' + (kind || 'ok'), text: msg });
  host.appendChild(t);
  setTimeout(function() {
    if (t.parentNode) t.parentNode.removeChild(t);
  }, 4000);
}

function getWebKey() {
  const url = new URL(window.location.href);
  const tokenFromUrl = url.searchParams.get('token');
  if (tokenFromUrl) {
    try { localStorage.setItem('krab_web_key', tokenFromUrl); } catch (e) {}
    return tokenFromUrl;
  }
  try { return localStorage.getItem('krab_web_key') || ''; } catch (e) { return ''; }
}

function ageClass(ageH) {
  if (ageH > 24) return 'age-stale';
  if (ageH > 12) return 'age-warn';
  return 'age-fresh';
}

function renderKinds(rows) {
  const tbody = document.getElementById('kinds-tbody');
  tbody.textContent = '';
  if (!rows || rows.length === 0) {
    const tr = el('tr');
    tr.appendChild(el('td', { class: 'empty', text: 'No items', colspan: '4' }));
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = el('tr');
    const td0 = el('td');
    td0.appendChild(el('code', { text: r.kind }));
    tr.appendChild(td0);
    tr.appendChild(el('td', { class: 'num', text: fmtInt(r.open || 0) }));
    tr.appendChild(el('td', { class: 'num', text: fmtInt(r.acked || 0) }));
    tr.appendChild(el('td', { class: 'num', text: fmtInt(r.done || 0) }));
    tbody.appendChild(tr);
  }
}

function renderItems(rows) {
  const tbody = document.getElementById('items-tbody');
  tbody.textContent = '';
  document.getElementById('items-count').textContent = fmtInt(rows ? rows.length : 0);
  if (!rows || rows.length === 0) {
    const tr = el('tr');
    tr.appendChild(el('td', { class: 'empty', text: 'No items match filter', colspan: '7' }));
    tbody.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = el('tr');
    if (r.is_stale) tr.className = 'stale-row';
    tr.appendChild(el('td', undefined, [el('code', { text: (r.item_id || '').slice(0, 12) })]));
    tr.appendChild(el('td', undefined, [el('code', { text: r.kind || '' })]));
    tr.appendChild(el('td', { class: 'sev-' + (r.severity || 'info'), text: r.severity || 'info' }));
    tr.appendChild(el('td', undefined,
      [el('span', { class: 'pill p-' + (r.status || 'open'), text: r.status || 'open' })]));
    tr.appendChild(el('td', { text: r.title || '' }));
    tr.appendChild(el('td', { class: 'num ' + ageClass(r.age_hours || 0),
                               text: (r.age_hours || 0).toFixed(1) }));
    const tdActions = el('td');
    for (const action of (r.actions || [])) {
      const btn = el('button', { 'data-id': r.item_id, 'data-action': action, text: action });
      btn.addEventListener('click', function() { itemAction(r.item_id, action); });
      tdActions.appendChild(btn);
    }
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }
}

async function itemAction(itemId, action) {
  const token = getWebKey();
  const statusMap = { 'ack': 'acked', 'done': 'done', 'cancel': 'cancelled' };
  const targetStatus = statusMap[action] || action;
  try {
    const url = '/api/inbox/update' + (token ? '?token=' + encodeURIComponent(token) : '');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Krab-Web-Key'] = token;
    const resp = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ item_id: itemId, status: targetStatus, actor: 'owner-ui' }),
    });
    const data = await resp.json();
    if (data.ok) {
      toast('Item ' + action + 'ed: ' + itemId.slice(0, 12), 'ok');
      refresh();
    } else {
      toast('Failed: ' + (data.detail || 'unknown'), 'err');
    }
  } catch (exc) {
    toast('Error: ' + exc, 'err');
  }
}

async function bulkAckStale() {
  const token = getWebKey();
  try {
    const url = '/api/inbox/bulk-ack-stale' + (token ? '?token=' + encodeURIComponent(token) : '');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Krab-Web-Key'] = token;
    const resp = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ age_threshold_hours: 12, target_status: 'acked',
                              actor: 'owner-ui', note: 'bulk_admin_ui' }),
    });
    const data = await resp.json();
    if (data.ok) {
      toast('Bulk-ack OK: matched=' + (data.result && data.result.matched), 'ok');
      refresh();
    } else {
      toast('Failed: ' + (data.detail || 'unknown'), 'err');
    }
  } catch (exc) {
    toast('Error: ' + exc, 'err');
  }
}

async function cleanupStale() {
  const token = getWebKey();
  try {
    const url = '/api/inbox/cleanup-stale?max_age_days=7'
              + (token ? '&token=' + encodeURIComponent(token) : '');
    const headers = {};
    if (token) headers['X-Krab-Web-Key'] = token;
    const resp = await fetch(url, { method: 'POST', headers: headers });
    const data = await resp.json();
    if (data.ok) {
      toast('Cleanup OK: archived=' + (data.archived || 0), 'ok');
      refresh();
    } else {
      toast('Failed: ' + (data.detail || 'unknown'), 'err');
    }
  } catch (exc) {
    toast('Error: ' + exc, 'err');
  }
}

async function cancelLowSeverity() {
  const token = getWebKey();
  try {
    const url = '/api/inbox/bulk-ack-stale' + (token ? '?token=' + encodeURIComponent(token) : '');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Krab-Web-Key'] = token;
    const resp = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ severity: 'info', age_threshold_hours: 12,
                              target_status: 'cancelled', actor: 'owner-ui',
                              note: 'bulk_cancel_low_severity' }),
    });
    const data = await resp.json();
    if (data.ok) {
      toast('Cancel-low OK: matched=' + (data.result && data.result.matched), 'ok');
      refresh();
    } else {
      toast('Failed: ' + (data.detail || 'unknown'), 'err');
    }
  } catch (exc) {
    toast('Error: ' + exc, 'err');
  }
}

async function refresh() {
  try {
    const status = document.getElementById('f-status').value || 'open';
    const kind = document.getElementById('f-kind').value || '';
    const limit = document.getElementById('f-limit').value || '50';
    const params = new URLSearchParams({ status: status, limit: limit });
    if (kind) params.set('kind', kind);
    const resp = await fetch('/api/admin/inbox/dashboard?' + params.toString());
    const data = await resp.json();
    if (!data.ok) {
      document.getElementById('last-refresh').textContent = 'error';
      return;
    }
    const s = data.stats || {};
    document.getElementById('stat-open').textContent = fmtInt(s.total_open || 0);
    document.getElementById('stat-stale').textContent = fmtInt(s.stale_open || 0);
    document.getElementById('stat-acked').textContent = fmtInt(s.acked || 0);
    document.getElementById('stat-done').textContent = fmtInt(s.done || 0);
    document.getElementById('stat-cancelled').textContent = fmtInt(s.cancelled || 0);
    document.getElementById('stat-attention').textContent = fmtInt(s.attention || 0);

    renderKinds(data.kinds || []);
    renderItems(data.items || []);

    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch (exc) {
    document.getElementById('last-refresh').textContent = 'error: ' + exc;
  }
}

document.getElementById('btn-apply-filter').addEventListener('click', refresh);
document.getElementById('btn-ack-stale').addEventListener('click', bulkAckStale);
document.getElementById('btn-cleanup-stale').addEventListener('click', cleanupStale);
document.getElementById('btn-cancel-low').addEventListener('click', cancelLowSeverity);

refresh();
setInterval(refresh, 20000);
</script>
</body>
</html>
"""
