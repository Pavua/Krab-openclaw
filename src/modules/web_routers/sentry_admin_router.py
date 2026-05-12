# -*- coding: utf-8 -*-
"""
Sentry admin router — Wave 164 (Session 48).

Owner-panel страница ``/admin/sentry`` + JSON API для live-мониторинга
Sentry events + recent issues + квота с one-click resolve кнопками.

Endpoints:
- GET  /api/admin/sentry/dashboard       — JSON: квота + recent issues +
                                            resolved_count_24h
- POST /api/admin/sentry/issue/{id}/resolve — write-access: PUT issue → resolved
- GET  /admin/sentry                      — HTML страница с polling 30s

Источники данных:
- Sentry HTTP API (sentry.io/api/0) — SENTRY_AUTH_TOKEN + SENTRY_ORG_SLUG
- ``~/.openclaw/krab_runtime_state/sentry_quota_baseline.json`` (Wave 71)
  → недельная база events для расчёта delta-квоты
- ``~/.openclaw/krab_runtime_state/sentry_resolver.log`` — log авто-резолвера
  для подсчёта resolved_count_24h

Реализация подсмотрена в:
- ``scripts/agent_tools/krab_sentry.py`` (httpx wrapper)
- ``scripts/sentry_stale_resolver.py`` (resolve_issue PUT pattern)
- ``scripts/sentry_poll_direct.py`` (issues list fetch)

См. ``src/modules/web_routers/admin_router.py`` для общего pattern Phase 2 routing.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi import Path as FPath
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.logger import get_logger

from ._context import RouterContext

logger = get_logger("sentry_admin_router")

# ── Конфигурация ────────────────────────────────────────────────────────────

SENTRY_API_BASE = "https://sentry.io/api/0"
DEFAULT_TIMEOUT_SEC = 15.0
ISSUES_DEFAULT_LIMIT = 20
WEEKLY_QUOTA_LIMIT_DEFAULT = 5000  # бесплатный план Sentry дает 5k events/мес

_RUNTIME_STATE = Path.home() / ".openclaw" / "krab_runtime_state"
_QUOTA_BASELINE = _RUNTIME_STATE / "sentry_quota_baseline.json"
_RESOLVER_LOG = _RUNTIME_STATE / "sentry_resolver.log"


# ── Helpers (модуль-level, чтобы тесты могли patch) ─────────────────────────


def _sentry_token() -> str:
    """Возвращает SENTRY_AUTH_TOKEN из env, "" если не задан."""
    return (os.getenv("SENTRY_AUTH_TOKEN") or "").strip()


def _sentry_org() -> str:
    """Возвращает SENTRY_ORG_SLUG (default po-zm)."""
    return (os.getenv("SENTRY_ORG_SLUG") or "po-zm").strip()


def _sentry_projects() -> list[str]:
    """Список Sentry проектов: ENV SENTRY_PROJECTS (whitespace-separated)."""
    raw = os.getenv("SENTRY_PROJECTS", "python-fastapi krab-ear-agent krab-ear-backend")
    return [p for p in raw.split() if p.strip()]


def _weekly_quota_limit() -> int:
    """Месячный (или недельный) лимит — настраивается через KRAB_SENTRY_QUOTA_LIMIT."""
    raw = os.getenv("KRAB_SENTRY_QUOTA_LIMIT", str(WEEKLY_QUOTA_LIMIT_DEFAULT))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return WEEKLY_QUOTA_LIMIT_DEFAULT


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "krab-admin-panel/wave-164",
    }


def _load_baseline() -> dict[str, Any]:
    """Читает Wave 71 baseline JSON. Graceful → {} при отсутствии/ошибке."""
    if not _QUOTA_BASELINE.exists():
        return {}
    try:
        return json.loads(_QUOTA_BASELINE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentry_baseline_read_failed", error=str(exc))
        return {}


def _count_resolver_actions_last_24h() -> int:
    """Парсит sentry_resolver.log и считает строки `resolved issue_id=…` за 24h."""
    if not _RESOLVER_LOG.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pattern = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+resolved\s+issue_id=")
    count = 0
    try:
        with _RESOLVER_LOG.open("r", encoding="utf-8") as fh:
            for line in fh:
                m = pattern.match(line.strip())
                if not m:
                    continue
                ts_raw = m.group("ts")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                if ts >= cutoff:
                    count += 1
    except OSError as exc:
        logger.warning("sentry_resolver_log_read_failed", error=str(exc))
    return count


def _fetch_project_issues(
    token: str,
    org: str,
    project: str,
    *,
    limit: int = ISSUES_DEFAULT_LIMIT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """GET unresolved issues одного проекта. Graceful [] при HTTP ошибке."""
    url = f"{SENTRY_API_BASE}/projects/{org}/{project}/issues/"
    params = {"query": "is:unresolved", "statsPeriod": "14d", "limit": str(limit)}
    headers = _auth_headers(token)
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "sentry_issues_http_error",
                status=resp.status_code,
                project=project,
                body=resp.text[:200],
            )
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        return data
    except httpx.HTTPError as exc:
        logger.warning("sentry_issues_fetch_failed", project=project, error=str(exc))
        return []
    finally:
        if owns_client:
            client.close()


def _fetch_project_stats(
    token: str,
    org: str,
    project: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> int:
    """Возвращает total events count за 7d. 0 при ошибке."""
    url = f"{SENTRY_API_BASE}/projects/{org}/{project}/stats/"
    since_ts = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    params = {"stat": "received", "resolution": "1d", "since": str(since_ts)}
    headers = _auth_headers(token)
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code >= 400:
            return 0
        data = resp.json()
        if not isinstance(data, list):
            return 0
        # data: [[timestamp, count], ...]
        return sum(int(row[1]) for row in data if isinstance(row, list) and len(row) >= 2)
    except httpx.HTTPError:
        return 0
    finally:
        if owns_client:
            client.close()


def _resolve_issue_via_api(
    token: str,
    org: str,
    issue_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """PUT issue → resolved. Возвращает dict с ok/status/error."""
    # Wave 42-A-fix: Sentry требует org-prefixed URL для PUT
    url = f"{SENTRY_API_BASE}/organizations/{org}/issues/{issue_id}/"
    headers = {
        **_auth_headers(token),
        "Content-Type": "application/json",
    }
    payload = {"status": "resolved", "statusDetails": {}}
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.put(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}",
                "body": resp.text[:200],
            }
        data = resp.json()
        return {"ok": True, "status": data.get("status"), "issue_id": issue_id}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if owns_client:
            client.close()


def _summarize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Нормализует raw Sentry issue в плоский dict для UI."""
    return {
        "id": str(issue.get("id") or ""),
        "short_id": issue.get("shortId") or "",
        "title": issue.get("title") or "(no title)",
        "level": issue.get("level") or "error",
        "count": int(issue.get("count") or 0),
        "user_count": int(issue.get("userCount") or 0),
        "last_seen": issue.get("lastSeen") or "",
        "status": issue.get("status") or "unresolved",
        "project": (issue.get("project") or {}).get("slug") or "",
        "permalink": issue.get("permalink") or "",
    }


def _collect_dashboard_payload() -> dict[str, Any]:
    """Главный агрегатор: квота + recent issues + resolved_24h.

    Если SENTRY_AUTH_TOKEN не задан — возвращает skeleton с пустыми данными
    и ``available=False``, чтобы UI могла показать "Sentry not configured".
    """
    token = _sentry_token()
    if not token:
        return {
            "ok": True,
            "available": False,
            "reason": "SENTRY_AUTH_TOKEN_missing",
            "weekly_quota_used": 0,
            "weekly_quota_limit": _weekly_quota_limit(),
            "recent_issues": [],
            "resolved_count_24h": 0,
            "baseline": _load_baseline(),
        }

    org = _sentry_org()
    projects = _sentry_projects()
    baseline = _load_baseline()

    issues_all: list[dict[str, Any]] = []
    total_events_7d = 0
    errors_per_project: dict[str, str] = {}

    # Один shared client для batched fetch — экономит TCP handshake.
    with httpx.Client(timeout=DEFAULT_TIMEOUT_SEC) as client:
        for project in projects:
            project_issues = _fetch_project_issues(
                token, org, project, limit=ISSUES_DEFAULT_LIMIT, client=client
            )
            for issue in project_issues:
                # Прикрепляем project slug если Sentry не вернул его в payload
                if not (issue.get("project") or {}).get("slug"):
                    issue.setdefault("project", {})["slug"] = project
                issues_all.append(issue)
            try:
                total_events_7d += _fetch_project_stats(token, org, project, client=client)
            except Exception as exc:  # noqa: BLE001
                errors_per_project[project] = str(exc)

    # Сортируем все issues по lastSeen DESC и ограничиваем до 20
    def _sort_key(item: dict[str, Any]) -> str:
        return str(item.get("lastSeen") or "")

    issues_all.sort(key=_sort_key, reverse=True)
    summary_issues = [_summarize_issue(i) for i in issues_all[:ISSUES_DEFAULT_LIMIT]]

    return {
        "ok": True,
        "available": True,
        "org": org,
        "projects": projects,
        "weekly_quota_used": total_events_7d,
        "weekly_quota_limit": _weekly_quota_limit(),
        "recent_issues": summary_issues,
        "resolved_count_24h": _count_resolver_actions_last_24h(),
        "baseline": baseline,
        "errors": errors_per_project,
    }


# ── HTML страница (inline, без template engine) ──────────────────────────────
# JS использует только textContent + DOM API (createElement/setAttribute) для
# защиты от XSS — никакого innerHTML с шаблонной интерполяцией.

_ADMIN_SENTRY_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Krab · Sentry Admin</title>
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
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
  .card { background: var(--card); border: 1px solid var(--border); padding: 16px;
          border-radius: 6px; }
  .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase;
                 letter-spacing: 0.6px; }
  .card .value { font-size: 28px; font-weight: 600; margin-top: 6px;
                 font-family: "SF Mono", Menlo, monospace; }
  .gauge { height: 6px; background: #1a1a1a; border-radius: 3px; margin-top: 12px;
           overflow: hidden; }
  .gauge .bar { height: 100%; background: var(--ok); transition: width .3s, background .3s; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  th, td { padding: 10px 12px; text-align: left; font-size: 13px; border-bottom:
           1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; text-transform: uppercase;
       letter-spacing: 0.5px; font-size: 11px; background: #101010; }
  tr:last-child td { border-bottom: none; }
  .level-error { color: var(--danger); }
  .level-warning { color: var(--warn); }
  .level-fatal { color: var(--danger); font-weight: 600; }
  .level-info { color: var(--muted); }
  button.resolve {
    background: var(--accent); color: #fff; border: none; padding: 6px 12px;
    border-radius: 4px; cursor: pointer; font-size: 12px;
  }
  button.resolve:hover { background: #ea580c; }
  button.resolve:disabled { background: #444; cursor: not-allowed; }
  .empty { padding: 40px; text-align: center; color: var(--muted); }
  .status-line { color: var(--muted); font-size: 12px; margin-bottom: 12px; }
  .mono { font-family: "SF Mono", Menlo, monospace; }
  a.permalink { color: var(--accent); text-decoration: none; }
  a.permalink:hover { text-decoration: underline; }
</style>
</head>
<body>
<header>
  <h1>Sentry Admin</h1>
  <a href="/">Главная</a>
  <a href="/stats">Stats</a>
  <a href="/inbox">Inbox</a>
  <a href="/costs">Costs</a>
  <span class="status-line" id="status">загрузка...</span>
</header>

<div class="grid">
  <div class="card">
    <div class="label">Weekly events used</div>
    <div class="value mono" id="quota-used">-</div>
    <div class="gauge"><div class="bar" id="quota-bar" style="width:0%"></div></div>
    <div class="label" id="quota-pct" style="margin-top:8px">- / -</div>
  </div>
  <div class="card">
    <div class="label">Recent unresolved issues</div>
    <div class="value mono" id="issues-count">-</div>
  </div>
  <div class="card">
    <div class="label">Resolved last 24h</div>
    <div class="value mono" id="resolved-24h">-</div>
  </div>
</div>

<table id="issues-table">
  <thead>
    <tr>
      <th>Level</th>
      <th>Title</th>
      <th>Project</th>
      <th class="mono">Count</th>
      <th class="mono">Last seen</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody id="issues-body"></tbody>
</table>

<script>
  function fmtLastSeen(iso) {
    if (!iso) return '-';
    try {
      const d = new Date(iso);
      const secs = Math.floor((Date.now() - d.getTime()) / 1000);
      if (secs < 60) return secs + 's ago';
      if (secs < 3600) return Math.floor(secs/60) + 'm ago';
      if (secs < 86400) return Math.floor(secs/3600) + 'h ago';
      return Math.floor(secs/86400) + 'd ago';
    } catch (e) { return String(iso).substring(0, 19); }
  }

  function levelClass(lvl) {
    const l = String(lvl || '').toLowerCase();
    if (l === 'fatal') return 'level-fatal';
    if (l === 'error') return 'level-error';
    if (l === 'warning') return 'level-warning';
    return 'level-info';
  }

  function gaugeColor(pct) {
    if (pct >= 90) return 'var(--danger)';
    if (pct >= 70) return 'var(--warn)';
    return 'var(--ok)';
  }

  function emptyRow(text) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.setAttribute('colspan', '6');
    td.className = 'empty';
    td.textContent = text;
    tr.appendChild(td);
    return tr;
  }

  function makeCell(text, cls) {
    const td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = String(text == null ? '-' : text);
    return td;
  }

  async function resolveIssue(id, btn) {
    btn.disabled = true;
    btn.textContent = '...';
    try {
      const resp = await fetch('/api/admin/sentry/issue/' + encodeURIComponent(id) + '/resolve',
                                { method: 'POST' });
      if (resp.ok) {
        btn.textContent = 'OK';
        const row = btn.closest('tr');
        if (row) row.style.opacity = '0.4';
        setTimeout(() => refresh(), 800);
      } else {
        btn.textContent = 'fail';
        btn.disabled = false;
      }
    } catch (e) {
      btn.textContent = 'err';
      btn.disabled = false;
    }
  }

  function renderIssueRow(issue) {
    const tr = document.createElement('tr');
    // Level
    tr.appendChild(makeCell(String(issue.level || '?').toUpperCase(), levelClass(issue.level)));
    // Title (with permalink as <a> if available)
    const tdTitle = document.createElement('td');
    if (issue.permalink) {
      const a = document.createElement('a');
      a.className = 'permalink';
      a.href = issue.permalink;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = String(issue.title || '-');
      tdTitle.appendChild(a);
    } else {
      tdTitle.textContent = String(issue.title || '-');
    }
    tr.appendChild(tdTitle);
    // Project / Count / LastSeen
    tr.appendChild(makeCell(issue.project || '-', 'mono'));
    tr.appendChild(makeCell(issue.count || 0, 'mono'));
    tr.appendChild(makeCell(fmtLastSeen(issue.last_seen), 'mono'));
    // Action button
    const tdAct = document.createElement('td');
    const btn = document.createElement('button');
    btn.className = 'resolve';
    btn.textContent = 'Resolve';
    btn.setAttribute('data-id', String(issue.id || ''));
    btn.addEventListener('click', function () {
      resolveIssue(String(issue.id || ''), btn);
    });
    tdAct.appendChild(btn);
    tr.appendChild(tdAct);
    return tr;
  }

  async function refresh() {
    try {
      const resp = await fetch('/api/admin/sentry/dashboard');
      const data = await resp.json();
      const used = Number(data.weekly_quota_used) || 0;
      const limit = Number(data.weekly_quota_limit) || 1;
      const pct = Math.min(100, Math.round(100 * used / limit));
      document.getElementById('quota-used').textContent = used.toLocaleString();
      document.getElementById('quota-pct').textContent = used + ' / ' + limit + ' (' + pct + '%)';
      const bar = document.getElementById('quota-bar');
      bar.style.width = pct + '%';
      bar.style.background = gaugeColor(pct);
      const issues = Array.isArray(data.recent_issues) ? data.recent_issues : [];
      document.getElementById('issues-count').textContent = issues.length;
      document.getElementById('resolved-24h').textContent = Number(data.resolved_count_24h) || 0;

      const body = document.getElementById('issues-body');
      // Очищаем безопасно через replaceChildren API.
      body.replaceChildren();
      if (!data.available) {
        body.appendChild(emptyRow('Sentry не настроен (нет SENTRY_AUTH_TOKEN)'));
      } else if (issues.length === 0) {
        body.appendChild(emptyRow('Нет unresolved issues'));
      } else {
        for (const issue of issues) {
          body.appendChild(renderIssueRow(issue));
        }
      }
      document.getElementById('status').textContent =
        'обновлено ' + new Date().toLocaleTimeString('ru-RU', { hour12: false });
    } catch (e) {
      document.getElementById('status').textContent = 'ошибка: ' + e.message;
    }
  }

  // Стартовая загрузка + polling каждые 30s
  document.getElementById('issues-body').appendChild(emptyRow('загрузка...'));
  refresh();
  setInterval(refresh, 30000);
</script>
</body>
</html>
"""


# ── Router factory ──────────────────────────────────────────────────────────


def build_sentry_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с /api/admin/sentry/* + /admin/sentry HTML."""
    router = APIRouter(tags=["sentry-admin"])

    @router.get("/api/admin/sentry/dashboard")
    async def sentry_dashboard() -> JSONResponse:
        """JSON: квота + последние 20 issues + resolved_24h."""
        try:
            payload = _collect_dashboard_payload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentry_dashboard_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"sentry_dashboard_failed: {exc}") from exc
        return JSONResponse(payload)

    @router.post("/api/admin/sentry/issue/{issue_id}/resolve")
    async def sentry_resolve(
        issue_id: str = FPath(..., description="Sentry issue ID"),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """One-click resolve: PUT issue → resolved.

        Защищено через ``ctx.assert_write_access_fn`` — owner-only.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        sentry_token = _sentry_token()
        if not sentry_token:
            raise HTTPException(status_code=503, detail="sentry_not_configured")

        # Простая валидация issue_id: Sentry IDs — числовые/alphanumeric строки
        if not issue_id or not issue_id.replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid_issue_id")

        org = _sentry_org()
        result = _resolve_issue_via_api(sentry_token, org, issue_id)
        if not result.get("ok"):
            logger.warning(
                "sentry_resolve_failed",
                issue_id=issue_id,
                error=result.get("error"),
            )
            raise HTTPException(
                status_code=502,
                detail=f"sentry_resolve_failed: {result.get('error', 'unknown')}",
            )

        # Лог в black_box для audit trail если доступен
        black_box = ctx.get_dep("black_box")
        if black_box and hasattr(black_box, "log_event"):
            try:
                black_box.log_event("sentry_admin_resolve", f"issue_id={issue_id}")
            except Exception:  # noqa: BLE001
                pass

        logger.info("sentry_admin_resolve_ok", issue_id=issue_id)
        return {"ok": True, "issue_id": issue_id, "status": result.get("status", "resolved")}

    @router.get("/admin/sentry", response_class=HTMLResponse)
    async def sentry_admin_page() -> HTMLResponse:
        """HTML страница с polling 30s + кнопками resolve."""
        return HTMLResponse(
            _ADMIN_SENTRY_HTML,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return router
