# -*- coding: utf-8 -*-
"""
Swarm admin router — Wave 152.

Объединённый dashboard свёрма в Owner Panel ``:8080``. В отличие от
``swarm_router.py`` (множество мелких read/write endpoints для команд
управления свёрмом и Kanban CRUD), этот модуль предоставляет один
агрегирующий endpoint для UI + HTML страницу с polling.

Источники данных (Wave 89 + существующие):
- ``swarm_activity_log`` (Wave 89, SQLite) — running/done/failed runs +
  per-team stats (count, avg_latency, success_rate).
- ``swarm_task_board`` (singleton, JSON) — Kanban: pending / in_progress /
  done / failed / blocked.

Endpoints:
- GET /api/admin/swarm/dashboard — JSON: active / stats / recent / board.
- GET /admin/swarm                — HTML страница (inline, polling 10s).

Контракт ``/api/admin/swarm/dashboard``::

    {
      "ok": true,
      "active": [{"id", "team", "topic", "started_ts", "started_ago_sec"}],
      "stats": {team: {count, done, failed, started, avg_latency_ms,
                       success_rate}},
      "recent": [{"id", "ts", "team", "topic", "status", "latency_ms",
                  "artifact_ref", "errors"}],
      "board": {
        "summary": {"total", "by_status", "by_team"},
        "columns": {
          "pending":     [task_dict, ...],
          "in_progress": [...],
          "done":        [...],
          "failed":      [...],
          "blocked":     [...]
        }
      }
    }
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ._context import RouterContext

# Колонки Kanban в фиксированном порядке (UI рендерит left → right).
_BOARD_COLUMNS: tuple[str, ...] = (
    "pending",
    "in_progress",
    "done",
    "failed",
    "blocked",
)

# Сколько задач показывать в каждой колонке (FIFO ограничение для UI).
_BOARD_COLUMN_LIMIT = 25

# Сколько recent completed runs показывать.
_RECENT_LIMIT = 20

# Limit запроса в activity_log для отбора running tasks. Все running ≥30 мин
# назад считаются stale (UI помечает их особо). Берём с запасом, чтобы fresh
# running не вытеснялись завершёнными.
_ACTIVE_QUERY_LIMIT = 100


def _collect_active_runs(activity_log: Any) -> list[dict[str, Any]]:
    """Извлекает running (status='started') из activity_log + computes age.

    Возвращает [{id, team, topic, started_ts, started_ago_sec}, ...]
    в порядке: самые недавние первыми (как и query_recent).
    """
    try:
        rows = activity_log.query_recent(limit=_ACTIVE_QUERY_LIMIT)
    except Exception:  # noqa: BLE001
        return []

    now_ts = int(time.time())
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() != "started":
            continue
        try:
            started_ts = int(row.get("ts") or 0)
        except (TypeError, ValueError):
            started_ts = 0
        out.append(
            {
                "id": row.get("id"),
                "team": str(row.get("team") or ""),
                "topic": str(row.get("topic") or ""),
                "started_ts": started_ts,
                "started_ago_sec": max(0, now_ts - started_ts) if started_ts else None,
            }
        )
    return out


def _collect_recent(activity_log: Any) -> list[dict[str, Any]]:
    """Возвращает последние ``_RECENT_LIMIT`` записей (все статусы)."""
    try:
        rows = activity_log.query_recent(limit=_RECENT_LIMIT)
    except Exception:  # noqa: BLE001
        return []
    # Pass-through; query_recent уже возвращает копии dict.
    return [r for r in rows if isinstance(r, dict)]


def _collect_stats(activity_log: Any) -> dict[str, dict[str, Any]]:
    """Возвращает per-team stats; пустой dict при ошибке."""
    try:
        stats = activity_log.stats_by_team()
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(stats, dict):
        return {}
    return stats


def _collect_board(task_board: Any) -> dict[str, Any]:
    """Возвращает summary + 5 колонок Kanban (pending/in_progress/done/failed/blocked).

    Каждая колонка — list of task dicts, отсортированный по updated_at desc
    (новые первыми, как и list_tasks).
    """
    try:
        summary = task_board.get_board_summary()
    except Exception:  # noqa: BLE001
        summary = {"total": 0, "by_status": {}, "by_team": {}}

    columns: dict[str, list[dict[str, Any]]] = {col: [] for col in _BOARD_COLUMNS}
    for status in _BOARD_COLUMNS:
        try:
            tasks = task_board.list_tasks(status=status, limit=_BOARD_COLUMN_LIMIT)
        except Exception:  # noqa: BLE001
            tasks = []
        # list_tasks возвращает list[SwarmTask] — конвертируем в dict для JSON.
        col_items: list[dict[str, Any]] = []
        for t in tasks or []:
            try:
                col_items.append(asdict(t))
            except (TypeError, ValueError):
                # На случай если list_tasks вернул dict (defensive — не должно
                # происходить в текущей реализации, но фьючерсо-устойчиво).
                if isinstance(t, dict):
                    col_items.append(dict(t))
        columns[status] = col_items

    return {"summary": summary, "columns": columns}


def build_swarm_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с swarm admin dashboard."""
    router = APIRouter(tags=["swarm-admin"])

    # ---------- GET /api/admin/swarm/dashboard --------------------------------
    @router.get("/api/admin/swarm/dashboard")
    async def admin_swarm_dashboard() -> dict[str, Any]:
        """Агрегирующий endpoint: active runs + stats + recent + Kanban board."""
        # Ленивый импорт — singleton может быть не инициализирован в момент
        # создания router (bootstrap configure_default_path может ещё не
        # выполниться). Импорт каждый call — дёшево, singleton кэшируется.
        from src.core.swarm_activity_log import (  # noqa: PLC0415
            swarm_activity_log as _activity_log,
        )
        from src.core.swarm_task_board import (  # noqa: PLC0415
            swarm_task_board as _task_board,
        )

        active = _collect_active_runs(_activity_log)
        stats = _collect_stats(_activity_log)
        recent = _collect_recent(_activity_log)
        board = _collect_board(_task_board)

        return {
            "ok": True,
            "active": active,
            "stats": stats,
            "recent": recent,
            "board": board,
        }

    # ---------- GET /admin/swarm ----------------------------------------------
    @router.get("/admin/swarm", response_class=HTMLResponse)
    async def admin_swarm_page() -> HTMLResponse:
        """HTML страница swarm dashboard (Kanban + activity log)."""
        return HTMLResponse(_SWARM_PAGE_HTML, headers={"Cache-Control": "no-store"})

    return router


# ── Inline HTML template ────────────────────────────────────────────────────
# Все значения от сервера рендерятся через .textContent / DOM API — никакого
# innerHTML, защищаемся от XSS даже если topic/team прилетят с user input.

_SWARM_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Krab — Swarm Dashboard</title>
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
    --pending: #6c757d;
    --in_progress: #58a6ff;
    --done: #2ea043;
    --failed: #f85149;
    --blocked: #d29922;
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
  section { background: var(--card); border: 1px solid var(--border);
            border-radius: 8px; padding: 16px; margin-bottom: 20px; }
  section h2 { margin: 0 0 12px 0; font-size: 14px; color: var(--muted);
               text-transform: uppercase; letter-spacing: 1px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 8px 12px; text-align: left;
           border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: normal; font-size: 11px;
       text-transform: uppercase; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
           font-size: 11px; font-weight: 600; }
  .b-running { background: rgba(88,166,255,0.18); color: var(--accent); }
  .b-done { background: rgba(46,160,67,0.15); color: var(--ok); }
  .b-failed { background: rgba(248,81,73,0.15); color: var(--err); }
  .b-started { background: rgba(88,166,255,0.18); color: var(--accent); }
  .b-stale { background: rgba(210,153,34,0.15); color: var(--warn); }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px; }
  .stat-card { background: rgba(255,255,255,0.02); border: 1px solid var(--border);
               border-radius: 6px; padding: 12px; }
  .stat-team { font-weight: 600; color: var(--accent); margin-bottom: 6px;
               text-transform: uppercase; font-size: 12px; letter-spacing: 0.5px; }
  .stat-row { display: flex; justify-content: space-between; font-size: 12px;
              color: var(--muted); padding: 2px 0; }
  .stat-row .val { color: var(--fg); }
  .kanban { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .col { background: rgba(255,255,255,0.02); border: 1px solid var(--border);
         border-radius: 6px; padding: 10px; min-height: 140px; }
  .col-head { display: flex; justify-content: space-between; align-items: center;
              margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
  .col-title { font-weight: 600; font-size: 12px; text-transform: uppercase;
               letter-spacing: 0.5px; }
  .col-count { background: var(--border); color: var(--muted); padding: 1px 8px;
               border-radius: 10px; font-size: 11px; }
  .task-card { background: var(--card); border: 1px solid var(--border);
               border-radius: 5px; padding: 8px 10px; margin-bottom: 6px;
               font-size: 12px; }
  .task-title { font-weight: 600; margin-bottom: 4px; word-break: break-word; }
  .task-meta { color: var(--muted); font-size: 11px;
               display: flex; gap: 6px; flex-wrap: wrap; }
  .task-priority { padding: 1px 6px; border-radius: 3px; font-size: 10px; }
  .p-low { background: rgba(108,117,125,0.18); color: var(--muted); }
  .p-medium { background: rgba(88,166,255,0.18); color: var(--accent); }
  .p-high { background: rgba(210,153,34,0.18); color: var(--warn); }
  .p-critical { background: rgba(248,81,73,0.18); color: var(--err); }
  .col-pending .col-title { color: var(--pending); }
  .col-in_progress .col-title { color: var(--in_progress); }
  .col-done .col-title { color: var(--done); }
  .col-failed .col-title { color: var(--failed); }
  .col-blocked .col-title { color: var(--blocked); }
  .empty { color: var(--muted); font-style: italic; font-size: 12px;
           text-align: center; padding: 20px 0; }
  code { font-family: ui-monospace, monospace; font-size: 12px; }
  .topic { max-width: 380px; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; display: inline-block; vertical-align: middle; }
  .summary-row { display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px; }
  .summary-row .item { color: var(--muted); }
  .summary-row .item .val { color: var(--fg); font-weight: 600; margin-left: 6px; }
</style>
</head>
<body>
<header>
  <div style="display:flex; align-items:center; gap:18px;">
    <h1>Krab — Swarm Dashboard</h1>
    <nav class="tabs">
      <a href="/admin/models">Models</a>
      <a href="/admin/routing">Routing</a>
      <a href="/admin/ecosystem">Ecosystem</a>
      <a href="/admin/swarm" class="active">Swarm</a>
      <a href="/admin/costs">Costs</a>
    </nav>
  </div>
  <div style="color: var(--muted); font-size: 12px;">
    Refresh: <span id="last-refresh">—</span>
  </div>
</header>
<main>

  <section>
    <h2>Active runs</h2>
    <div id="active-content"></div>
  </section>

  <section>
    <h2>Stats per team</h2>
    <div id="stats-grid" class="stats-grid"></div>
  </section>

  <section>
    <h2>Task board (Kanban)</h2>
    <div class="summary-row" id="board-summary"></div>
    <div class="kanban" id="kanban"></div>
  </section>

  <section>
    <h2>Recent completed (last 20)</h2>
    <div id="recent-content"></div>
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
  if (children) for (const c of children) if (c) node.appendChild(c);
  return node;
}

function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function formatAgo(sec) {
  if (sec == null) return '?';
  if (sec < 60) return sec + 's ago';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
  return Math.floor(sec / 86400) + 'd ago';
}

function formatTs(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch (e) {
    return String(ts);
  }
}

function renderActive(active) {
  const root = document.getElementById('active-content');
  clearNode(root);
  if (!active || active.length === 0) {
    root.appendChild(el('div', { class: 'empty', text: 'No active swarm runs.' }));
    return;
  }
  const table = el('table');
  const thead = el('thead');
  const headRow = el('tr');
  for (const h of ['Team', 'Topic', 'Started', 'Status']) {
    headRow.appendChild(el('th', { text: h }));
  }
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el('tbody');
  for (const run of active) {
    const tr = el('tr');
    tr.appendChild(el('td', { text: run.team || '—' }));
    const topicTd = el('td');
    const topicSpan = el('span', { class: 'topic', text: run.topic || '—' });
    topicSpan.setAttribute('title', run.topic || '');
    topicTd.appendChild(topicSpan);
    tr.appendChild(topicTd);
    tr.appendChild(el('td', { text: formatAgo(run.started_ago_sec) }));
    const statusTd = el('td');
    const isStale = (run.started_ago_sec || 0) > 1800;  // 30 min
    statusTd.appendChild(el('span', {
      class: 'badge ' + (isStale ? 'b-stale' : 'b-running'),
      text: isStale ? 'stale' : 'running',
    }));
    tr.appendChild(statusTd);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  root.appendChild(table);
}

function renderStats(stats) {
  const grid = document.getElementById('stats-grid');
  clearNode(grid);
  const teams = Object.keys(stats || {});
  if (teams.length === 0) {
    grid.appendChild(el('div', { class: 'empty', text: 'No stats yet.' }));
    return;
  }
  teams.sort();
  for (const team of teams) {
    const s = stats[team] || {};
    const card = el('div', { class: 'stat-card' });
    card.appendChild(el('div', { class: 'stat-team', text: team }));
    function row(label, value) {
      const r = el('div', { class: 'stat-row' });
      r.appendChild(el('span', { text: label }));
      r.appendChild(el('span', { class: 'val', text: String(value) }));
      return r;
    }
    card.appendChild(row('count', s.count != null ? s.count : 0));
    card.appendChild(row('done', s.done != null ? s.done : 0));
    card.appendChild(row('failed', s.failed != null ? s.failed : 0));
    card.appendChild(row('avg latency', s.avg_latency_ms != null
      ? (s.avg_latency_ms + ' ms') : '—'));
    const sr = (s.success_rate != null) ? (Math.round(s.success_rate * 1000) / 10) + '%' : '—';
    card.appendChild(row('success rate', sr));
    grid.appendChild(card);
  }
}

function renderBoard(board) {
  const summary = (board && board.summary) || {};
  const summaryRow = document.getElementById('board-summary');
  clearNode(summaryRow);
  function summItem(label, value) {
    const item = el('div', { class: 'item' });
    item.appendChild(document.createTextNode(label + ':'));
    item.appendChild(el('span', { class: 'val', text: String(value) }));
    return item;
  }
  summaryRow.appendChild(summItem('total', summary.total || 0));
  const byStatus = summary.by_status || {};
  for (const st of ['pending', 'in_progress', 'done', 'failed', 'blocked']) {
    summaryRow.appendChild(summItem(st, byStatus[st] || 0));
  }

  const kanban = document.getElementById('kanban');
  clearNode(kanban);
  const columns = (board && board.columns) || {};
  for (const status of ['pending', 'in_progress', 'done', 'failed', 'blocked']) {
    const col = el('div', { class: 'col col-' + status });
    const head = el('div', { class: 'col-head' });
    head.appendChild(el('div', { class: 'col-title', text: status }));
    head.appendChild(el('div', { class: 'col-count',
                                 text: String((columns[status] || []).length) }));
    col.appendChild(head);
    const tasks = columns[status] || [];
    if (tasks.length === 0) {
      col.appendChild(el('div', { class: 'empty', text: '— empty —' }));
    } else {
      for (const t of tasks) {
        const card = el('div', { class: 'task-card' });
        card.appendChild(el('div', { class: 'task-title', text: t.title || '(no title)' }));
        const meta = el('div', { class: 'task-meta' });
        meta.appendChild(el('span', { text: t.team || '?' }));
        const pri = (t.priority || 'medium').toLowerCase();
        meta.appendChild(el('span', { class: 'task-priority p-' + pri, text: pri }));
        if (t.created_by) {
          meta.appendChild(el('span', { text: 'by ' + t.created_by }));
        }
        card.appendChild(meta);
        col.appendChild(card);
      }
    }
    kanban.appendChild(col);
  }
}

function renderRecent(recent) {
  const root = document.getElementById('recent-content');
  clearNode(root);
  if (!recent || recent.length === 0) {
    root.appendChild(el('div', { class: 'empty', text: 'No recent runs.' }));
    return;
  }
  const table = el('table');
  const thead = el('thead');
  const headRow = el('tr');
  for (const h of ['When', 'Team', 'Topic', 'Status', 'Latency']) {
    headRow.appendChild(el('th', { text: h }));
  }
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el('tbody');
  for (const row of recent) {
    const tr = el('tr');
    tr.appendChild(el('td', { text: formatTs(row.ts) }));
    tr.appendChild(el('td', { text: row.team || '—' }));
    const topicTd = el('td');
    const topicSpan = el('span', { class: 'topic', text: row.topic || '—' });
    topicSpan.setAttribute('title', row.topic || '');
    topicTd.appendChild(topicSpan);
    tr.appendChild(topicTd);
    const status = String(row.status || '').toLowerCase();
    const statusTd = el('td');
    statusTd.appendChild(el('span', { class: 'badge b-' + status, text: status || '?' }));
    tr.appendChild(statusTd);
    const lat = row.latency_ms;
    tr.appendChild(el('td', { text: lat != null ? (lat + ' ms') : '—' }));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  root.appendChild(table);
}

async function refresh() {
  try {
    const r = await fetch('/api/admin/swarm/dashboard', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (!data.ok) throw new Error('payload not ok');
    renderActive(data.active || []);
    renderStats(data.stats || {});
    renderBoard(data.board || {});
    renderRecent(data.recent || []);
    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('last-refresh').textContent = 'error: ' + e.message;
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>
"""
