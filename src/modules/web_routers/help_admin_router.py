# -*- coding: utf-8 -*-
"""
Help admin router — Wave 187 (Session 48).

Индекс-страница `/admin/help`, описывающая все 14 admin-страниц Krab:
их URL, Wave-номер, назначение, ключевые endpoints, иконку и подсказку
о том, когда страница полезна.

Lightweight by design:
- compile-time metadata (нет DB/httpx/Prometheus в hot path)
- "Recent Changes" — лёгкий `git log` subprocess (≤200ms на M-серии)
- статический HTML с table layout

Endpoints (READY):
- GET /api/admin/help/pages   — JSON метаданных для 14 страниц.
- GET /admin/help             — HTML индекс с таблицей + recent changes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.core.subprocess_env import clean_subprocess_env

from ._context import RouterContext

_logger = get_logger(__name__)

# Корень репозитория — для `git log`.
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ── Compile-time metadata (single source of truth) ────────────────────────────
#
# Порядок: hierarchical (operational core → ops → infra → observability →
# unified). Те же индексы используются в HTML-таблице.

_ADMIN_PAGES: list[dict[str, Any]] = [
    {
        "path": "/admin/models",
        "wave": 144,
        "emoji": "🤖",
        "title": "Models",
        "purpose": "Provider × model picker для primary/translator/fallback chain.",
        "endpoints": [
            "GET /api/model/status",
            "POST /api/model/select",
            "GET /api/admin/models/picker",
        ],
        "when": (
            "Переключить provider/model в runtime, посмотреть статус "
            "всех моделей, проверить fallback chain."
        ),
    },
    {
        "path": "/admin/swarm",
        "wave": 152,
        "emoji": "🐝",
        "title": "Swarm",
        "purpose": "Kanban-доска задач свёрма (traders/coders/analysts/creative).",
        "endpoints": [
            "GET /api/admin/swarm/board",
            "POST /api/admin/swarm/tasks/{id}/assign",
            "POST /api/admin/swarm/tasks/{id}/done",
        ],
        "when": ("Управлять задачами свёрма, видеть live статус 4 команд, переназначать роли."),
    },
    {
        "path": "/admin/costs",
        "wave": 155,
        "emoji": "💰",
        "title": "Costs",
        "purpose": "Daily/weekly бюджет: расходы по провайдерам и моделям.",
        "endpoints": [
            "GET /api/admin/costs/daily",
            "GET /api/admin/costs/weekly",
            "GET /api/admin/costs/by_provider",
        ],
        "when": (
            "Контролировать FinOps: смотреть spend, выявлять expensive-models и аномалии расхода."
        ),
    },
    {
        "path": "/admin/ecosystem",
        "wave": 156,
        "emoji": "🌐",
        "title": "Ecosystem",
        "purpose": "9 service cards + swarm teams: live состояние всего стека.",
        "endpoints": [
            "GET /api/admin/ecosystem/status",
            "GET /api/ecosystem/health",
            "GET /api/swarm/teams",
        ],
        "when": (
            "Bird's-eye view всей экосистемы Krab: gateway, MCPs, "
            "Voice Gateway, Krab Ear, browser, swarm."
        ),
    },
    {
        "path": "/admin/inbox",
        "wave": 157,
        "emoji": "📥",
        "title": "Inbox",
        "purpose": "Inbox items: список, bulk-actions, stale remediation.",
        "endpoints": [
            "GET /api/inbox/items",
            "POST /api/inbox/bulk-ack-stale",
            "POST /api/inbox/{id}/ack",
        ],
        "when": (
            "Разобрать inbox queue, ack-нуть устаревшие items, массовые операции над сообщениями."
        ),
    },
    {
        "path": "/admin/routing",
        "wave": 146,
        "emoji": "🚦",
        "title": "Routing",
        "purpose": "Smart routing decisions: stages, classifier, feedback loop.",
        "endpoints": [
            "GET /api/admin/routing/stats",
            "GET /api/admin/routing/decisions",
            "GET /api/chat_policy",
        ],
        "when": (
            "Диагностика Smart Routing pipeline: какие сообщения "
            "проходят/отсекаются, threshold tuning."
        ),
    },
    {
        "path": "/admin/cron",
        "wave": 165,
        "emoji": "⏱️",
        "title": "Cron",
        "purpose": "56 launchd-агентов: status, trigger/pause/resume actions.",
        "endpoints": [
            "GET /api/admin/cron/list",
            "POST /api/admin/cron/{label}/trigger",
            "POST /api/admin/cron/{label}/pause",
        ],
        "when": (
            "Управлять LaunchAgents: посмотреть schedule, last_run, "
            "exit_code; принудительно запустить job."
        ),
    },
    {
        "path": "/admin/sentry",
        "wave": 164,
        "emoji": "🛡️",
        "title": "Sentry",
        "purpose": "События Sentry: list, quota gauge, inline resolve buttons.",
        "endpoints": [
            "GET /api/admin/sentry/events",
            "GET /api/admin/sentry/quota",
            "POST /api/admin/sentry/{id}/resolve",
        ],
        "when": ("Триаж production-ошибок: разрешать issues одним кликом, следить за quota."),
    },
    {
        "path": "/admin/logs",
        "wave": 169,
        "emoji": "📋",
        "title": "Logs",
        "purpose": "Live structlog tail + level filter + grep + download.",
        "endpoints": [
            "GET /api/admin/logs/tail",
            "GET /api/admin/logs/download",
            "GET /api/admin/logs/grep",
        ],
        "when": (
            "Дебаг runtime: следить за live логом, фильтровать по level/event, грепать pattern."
        ),
    },
    {
        "path": "/admin/db",
        "wave": 176,
        "emoji": "🗃️",
        "title": "DB",
        "purpose": "SQLite stats: размер, integrity_check, WAL checkpoint, VACUUM.",
        "endpoints": [
            "GET /api/admin/db/stats",
            "POST /api/admin/db/integrity_check",
            "POST /api/admin/db/wal_checkpoint",
        ],
        "when": (
            "Health БД: проверить integrity, освободить WAL, оценить размер archive.db / memory."
        ),
    },
    {
        "path": "/admin/network",
        "wave": 179,
        "emoji": "🛰️",
        "title": "Network",
        "purpose": "MTProto session: DC, heartbeat, FloodWait, ping, DNS.",
        "endpoints": [
            "GET /api/admin/network/session",
            "GET /api/admin/network/ping",
            "GET /api/admin/network/dns",
        ],
        "when": ("Диагностика связности: split-brain, FloodWait history, DNS lookup, ping DC."),
    },
    {
        "path": "/admin/voice",
        "wave": 183,
        "emoji": "🎙️",
        "title": "Voice",
        "purpose": "TTS/STT state, Voice Gateway, Krab Ear; restart actions.",
        "endpoints": [
            "GET /api/admin/voice/status",
            "POST /api/admin/voice/gateway/restart",
            "POST /api/admin/voice/ear/restart",
        ],
        "when": ("Voice-контур: проверить TTS pipeline, STT cost, перезапустить Gateway/Ear."),
    },
    {
        "path": "/admin/memory",
        "wave": 184,
        "emoji": "🧠",
        "title": "Memory",
        "purpose": "RAG stats: archive.db rows, vec health, retrieval metrics.",
        "endpoints": [
            "GET /api/admin/memory/stats",
            "POST /api/admin/memory/search",
            "GET /api/admin/memory/retrieval_metrics",
        ],
        "when": (
            "Проверить состояние RAG-памяти: indexed rows, vec_chunks, recall@k; искать по корпусу."
        ),
    },
    {
        "path": "/admin/health",
        "wave": 186,
        "emoji": "🩺",
        "title": "Health",
        "purpose": "Unified single-pane-of-glass: traffic light + 7 cards.",
        "endpoints": [
            "GET /api/health",
            "GET /api/admin/health/aggregate",
            "GET /api/ecosystem/health",
        ],
        "when": ("ЕДИНСТВЕННАЯ страница которую нужно открыть утром: agg-статус всех subsystems."),
    },
]


def _git_recent_waves(limit: int = 10) -> list[dict[str, str]]:
    """Прочитать ``git log --oneline -20`` и вернуть до `limit` Wave-коммитов.

    Возвращает [{sha, subject}, ...]. На любую ошибку — пустой список.
    """
    try:
        proc = subprocess.run(
            ["/usr/bin/git", "log", "--oneline", "-20"],
            cwd=str(_REPO_ROOT),
            env=clean_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _logger.warning("help_admin.git_log_failed", error=str(exc))
        return []
    if proc.returncode != 0:
        return []

    result: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # Фильтр: показываем только Wave-коммиты — пользователю это
        # релевантно как "что менялось недавно".
        if " Wave " not in line and ": Wave" not in line.replace("\t", " "):
            # Допускаем "Wave NNN:" и "Wave NNN-X:" в любой позиции.
            if "Wave " not in line:
                continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        sha, subject = parts[0], parts[1]
        result.append({"sha": sha, "subject": subject})
        if len(result) >= limit:
            break
    return result


def build_help_admin_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с index-страницей admin-консоли."""
    router = APIRouter(tags=["help-admin"])

    # ── GET /api/admin/help/pages ───────────────────────────────────────────

    @router.get("/api/admin/help/pages")
    async def help_pages() -> dict:
        """Список метаданных 14 admin-страниц (compile-time константа).

        Используется и HTML-страницей, и программными клиентами
        (например, CLI или Telegram-команда `!help admin`).
        """
        return {
            "ok": True,
            "count": len(_ADMIN_PAGES),
            "pages": _ADMIN_PAGES,
            "recent_waves": _git_recent_waves(limit=10),
        }

    # ── GET /admin/help — HTML index page ───────────────────────────────────

    @router.get("/admin/help", response_class=HTMLResponse)
    async def help_admin_page() -> HTMLResponse:
        """HTML индекс — фетчит метаданные через клиентский JS."""
        return HTMLResponse(_HELP_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/help ────────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — XSS-safe,
# никаких innerHTML с user/git-данными.

_HELP_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Admin Help</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #7dd3fc;
            --accent-strong: #38bdf8;
            --ok: #22c55e;
            --warn: #facc15;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.45;
        }
        .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 24px;
            background: #000; border-bottom: 1px solid var(--border);
        }
        header h1 { margin: 0; font-size: 1.4rem; }
        header .meta { color: var(--text-muted); font-size: 0.85rem; }
        main { padding: 20px 24px 40px; max-width: 1280px; margin: 0 auto; }
        .quick-health {
            background: rgba(34, 197, 94, 0.08);
            border: 1px solid rgba(34, 197, 94, 0.4);
            border-radius: 6px;
            padding: 14px 18px;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .quick-health .emoji { font-size: 1.6rem; }
        .quick-health a {
            color: var(--ok);
            text-decoration: none;
            font-weight: 600;
        }
        .quick-health a:hover { text-decoration: underline; }
        h2.section {
            font-size: 1.05rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin: 24px 0 10px;
        }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.9rem;
        }
        th, td {
            padding: 10px 12px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.72rem;
            letter-spacing: 0.05em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125, 211, 252, 0.04); }
        td.emoji-cell { font-size: 1.4rem; width: 40px; text-align: center; }
        td.title-cell a {
            color: var(--accent);
            text-decoration: none;
            font-weight: 600;
        }
        td.title-cell a:hover { color: var(--accent-strong); text-decoration: underline; }
        td.path-cell { width: 160px; }
        td.wave-cell { width: 70px; }
        td.endpoints-cell { width: 320px; }
        .endpoints-list { margin: 0; padding-left: 14px; font-size: 0.78rem; }
        .endpoints-list li { color: var(--text-muted); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.72rem; font-weight: 500;
            background: rgba(125,211,252,0.12); color: var(--accent);
        }
        .recent-list { padding-left: 18px; margin: 0; }
        .recent-list li { padding: 3px 0; font-size: 0.86rem; }
        .recent-list .sha {
            color: var(--accent);
            margin-right: 8px;
        }
        .err-banner {
            color: #ef4444;
            background: rgba(239,68,68,0.08);
            padding: 12px;
            border-radius: 4px;
            margin-bottom: 14px;
        }
        footer {
            margin-top: 32px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
            color: var(--text-muted);
            font-size: 0.8rem;
            text-align: center;
        }
        footer a { color: var(--text-muted); }
    </style>
</head>
<body>
    <header>
        <h1>📚 Krab · Admin Help</h1>
        <div class="meta">Индекс 14 admin-страниц · Wave 187</div>
    </header>
    <main>
        <div class="quick-health">
            <div class="emoji">🩺</div>
            <div>
                <strong>Quick Health</strong>
                — <a href="/admin/health">/admin/health</a> единый
                single-pane-of-glass: traffic light + 7 subsystem cards.
                Открывайте его первым, остальные — по необходимости.
            </div>
        </div>

        <div id="err-banner"></div>

        <h2 class="section">Все admin-страницы (14)</h2>
        <table id="pages-table">
            <thead>
                <tr>
                    <th></th>
                    <th>Page</th>
                    <th>Path</th>
                    <th>Wave</th>
                    <th>Purpose · When to use</th>
                    <th>Key endpoints</th>
                </tr>
            </thead>
            <tbody id="pages-body"></tbody>
        </table>

        <h2 class="section">Recent changes (последние Wave-коммиты)</h2>
        <ol class="recent-list" id="recent-list">
            <li><span class="mono">…</span></li>
        </ol>

        <footer>
            Krab Admin Console · Wave 187 · 14 страниц зарегистрировано
        </footer>
    </main>
    <script>
        function mkText(tag, text, cls) {
            const el = document.createElement(tag);
            if (cls) el.className = cls;
            if (text !== undefined && text !== null) el.textContent = text;
            return el;
        }
        function mkEndpointsCell(endpoints) {
            const td = document.createElement('td');
            td.className = 'endpoints-cell';
            const ul = document.createElement('ul');
            ul.className = 'endpoints-list mono';
            (endpoints || []).forEach(function(ep) {
                const li = document.createElement('li');
                li.textContent = ep;
                ul.appendChild(li);
            });
            td.appendChild(ul);
            return td;
        }
        function mkPurposeCell(purpose, when) {
            const td = document.createElement('td');
            const p1 = mkText('div', purpose);
            p1.style.marginBottom = '4px';
            const p2 = mkText('div', when);
            p2.style.color = 'var(--text-muted)';
            p2.style.fontSize = '0.82rem';
            td.appendChild(p1);
            td.appendChild(p2);
            return td;
        }
        function mkTitleCell(page) {
            const td = document.createElement('td');
            td.className = 'title-cell';
            const a = document.createElement('a');
            a.href = page.path;
            a.textContent = page.title;
            td.appendChild(a);
            return td;
        }
        async function fetchPages() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/help/pages');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const pages = data.pages || [];
                const tbody = document.getElementById('pages-body');
                while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
                pages.forEach(function(p) {
                    const tr = document.createElement('tr');
                    tr.appendChild(mkText('td', p.emoji || '·', 'emoji-cell'));
                    tr.appendChild(mkTitleCell(p));
                    const pathTd = mkText('td', p.path, 'path-cell mono');
                    tr.appendChild(pathTd);
                    const waveTd = document.createElement('td');
                    waveTd.className = 'wave-cell';
                    const badge = mkText('span', String(p.wave), 'badge');
                    waveTd.appendChild(badge);
                    tr.appendChild(waveTd);
                    tr.appendChild(mkPurposeCell(p.purpose || '', p.when || ''));
                    tr.appendChild(mkEndpointsCell(p.endpoints));
                    tbody.appendChild(tr);
                });

                // Recent waves.
                const recentList = document.getElementById('recent-list');
                while (recentList.firstChild) recentList.removeChild(recentList.firstChild);
                const waves = data.recent_waves || [];
                if (waves.length === 0) {
                    const li = document.createElement('li');
                    li.textContent = '(git log недоступен)';
                    li.style.color = 'var(--text-muted)';
                    recentList.appendChild(li);
                } else {
                    waves.forEach(function(w) {
                        const li = document.createElement('li');
                        const sha = mkText('span', w.sha, 'sha mono');
                        li.appendChild(sha);
                        li.appendChild(document.createTextNode(w.subject || ''));
                        recentList.appendChild(li);
                    });
                }
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        fetchPages();
    </script>
</body>
</html>
"""
