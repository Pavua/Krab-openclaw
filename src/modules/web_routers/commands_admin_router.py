# -*- coding: utf-8 -*-
"""
Commands admin router — Wave 190 (Session 48).

Owner-dashboard со всеми зарегистрированными Telegram-командами Краба:
- метаданные из ``core.command_registry`` (имя, описание, категория,
  owner-only, aliases, stage)
- usage-счётчики из ``command_usage.json`` (через ``get_usage`` /
  ``_command_last_ts``)
- пользовательские алиасы из ``command_aliases.json`` (best-effort,
  не падает если файл отсутствует)

Lightweight by design:
- registry — singleton (in-process)
- usage / aliases читаются 1 раз / 30s через TTL-cache
- никаких DB/Prometheus/httpx в hot path
- read-only (никаких write actions в v1; toggle/disable можно
  добавить отдельной волной)

Endpoints (READY):
- GET /api/admin/commands/list           — JSON списка всех команд
                                            + per-command usage stats
                                            + custom aliases
- GET /api/admin/commands/usage_summary  — top-10, never-used, totals,
                                            breakdown по категориям
- GET /admin/commands                     — HTML страница, polling 30s
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Путь к persistent-стейту команд.
_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
_USAGE_FILE = _STATE_DIR / "command_usage.json"
_ALIASES_FILE = _STATE_DIR / "command_aliases.json"

# Cache: registry дёшев, но usage/aliases читаются c диска.
_CACHE_TTL_SEC = 30.0
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

# UI labels для категорий (порядок повторяет CommandRegistry.CATEGORY_ORDER
# + добавлены "scheduler"/"system"/"files"/"dev").
_CATEGORY_LABELS: dict[str, str] = {
    "basic": "📘 Basic",
    "ai": "🧠 AI",
    "models": "🤖 Models",
    "translator": "🌐 Translator",
    "swarm": "🐝 Swarm",
    "costs": "💰 Costs",
    "notes": "📝 Notes",
    "management": "📨 Management",
    "modes": "🎛️ Modes",
    "users": "👥 Users",
    "scheduler": "⏱️ Scheduler",
    "system": "⚙️ System",
    "files": "📎 Files",
    "dev": "🛠️ Dev",
}

# Окно "недавнего использования" для расчёта recent_used_count.
_RECENT_WINDOW_DAYS = 7


def _read_usage_file() -> dict[str, Any]:
    """Прочитать ``command_usage.json``. Возвращает {counts, last_ts}.

    Безопасно к отсутствию файла, повреждённому JSON, legacy формату.
    """
    if not _USAGE_FILE.exists():
        return {"counts": {}, "last_ts": {}}
    try:
        raw = _USAGE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("commands_admin.usage_read_failed", error=str(exc))
        return {"counts": {}, "last_ts": {}}

    if isinstance(data, dict) and "counts" in data:
        counts_raw = data.get("counts") or {}
        last_ts_raw = data.get("last_ts") or {}
    else:
        # Legacy формат: плоский {name: count}.
        counts_raw = data if isinstance(data, dict) else {}
        last_ts_raw = {}

    # Нормализуем типы (защита от подозрительного содержимого).
    counts: dict[str, int] = {}
    for k, v in counts_raw.items():
        try:
            counts[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    last_ts: dict[str, float] = {}
    for k, v in last_ts_raw.items():
        try:
            last_ts[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return {"counts": counts, "last_ts": last_ts}


def _read_aliases_file() -> dict[str, str]:
    """Прочитать ``command_aliases.json``: {alias_name: target_command}.

    Файл может отсутствовать (по умолчанию пользователь не определяет
    кастомные алиасы) — возвращаем {}.
    """
    if not _ALIASES_FILE.exists():
        return {}
    try:
        raw = _ALIASES_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("commands_admin.aliases_read_failed", error=str(exc))
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _load_registry_safe() -> list[dict[str, Any]]:
    """Прочитать registry.all() и превратить в список словарей.

    На любой ImportError или unexpected исключение — пустой список +
    warning в лог.
    """
    try:
        from src.core.command_registry import registry as _reg  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        _logger.warning("commands_admin.registry_import_failed", error=str(exc))
        return []
    try:
        return [cmd.to_dict() for cmd in _reg.all()]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("commands_admin.registry_serialize_failed", error=str(exc))
        return []


def _build_payload() -> dict[str, Any]:
    """Сшить registry + usage + aliases в payload.

    Возвращает {commands, summary, categories, aliases_custom, ts}.
    """
    registry_cmds = _load_registry_safe()
    usage_data = _read_usage_file()
    counts = usage_data["counts"]
    last_ts = usage_data["last_ts"]
    custom_aliases = _read_aliases_file()

    now = time.time()
    recent_cutoff = now - _RECENT_WINDOW_DAYS * 86400.0

    # Per-command merging.
    commands: list[dict[str, Any]] = []
    for cmd in registry_cmds:
        name = cmd["name"]
        usage_count = int(counts.get(name, 0))
        last_used_ts = last_ts.get(name)
        recent = bool(last_used_ts and last_used_ts >= recent_cutoff)

        # Кастомные алиасы, чей target = этой команде.
        custom_for_cmd = sorted(alias for alias, target in custom_aliases.items() if target == name)

        commands.append(
            {
                "name": name,
                "category": cmd.get("category", "unknown"),
                "description": cmd.get("description", ""),
                "usage": cmd.get("usage", ""),
                "owner_only": bool(cmd.get("owner_only", False)),
                "aliases": list(cmd.get("aliases", [])),
                "custom_aliases": custom_for_cmd,
                "stage": cmd.get("stage", "production"),
                "usage_count": usage_count,
                "last_used_ts": last_used_ts,
                "recent_used": recent,
            }
        )

    # Сортировка: по категории (CATEGORY_ORDER) затем по имени.
    cat_order = list(_CATEGORY_LABELS.keys())
    cat_index = {c: i for i, c in enumerate(cat_order)}

    def _sort_key(c: dict[str, Any]) -> tuple[int, str]:
        return (cat_index.get(c["category"], 99), c["name"])

    commands.sort(key=_sort_key)

    # Breakdown по категориям.
    by_category: dict[str, int] = {}
    for c in commands:
        by_category[c["category"]] = by_category.get(c["category"], 0) + 1

    # Top-10 по usage_count (только команды с count>0).
    used = [c for c in commands if c["usage_count"] > 0]
    used.sort(key=lambda c: (-c["usage_count"], c["name"]))
    top_10 = [
        {
            "name": c["name"],
            "category": c["category"],
            "usage_count": c["usage_count"],
            "last_used_ts": c["last_used_ts"],
        }
        for c in used[:10]
    ]

    never_used = sorted(c["name"] for c in commands if c["usage_count"] == 0)

    summary = {
        "total_commands": len(commands),
        "total_invocations": sum(c["usage_count"] for c in commands),
        "unique_commands_used": len(used),
        "never_used_count": len(never_used),
        "recent_used_count": sum(1 for c in commands if c["recent_used"]),
        "owner_only_count": sum(1 for c in commands if c["owner_only"]),
        "by_category": by_category,
        "top_10": top_10,
        "never_used": never_used,
        "recent_window_days": _RECENT_WINDOW_DAYS,
    }

    return {
        "ok": True,
        "ts": now,
        "commands": commands,
        "summary": summary,
        "categories": [
            {"key": k, "label": _CATEGORY_LABELS[k], "count": by_category.get(k, 0)}
            for k in cat_order
            if by_category.get(k, 0) > 0
        ],
        "custom_aliases_total": len(custom_aliases),
    }


def _cached_payload() -> dict[str, Any]:
    """TTL-cache wrapper вокруг ``_build_payload``."""
    now = time.time()
    payload = _cache.get("payload")
    if payload is not None and now - _cache.get("ts", 0.0) < _CACHE_TTL_SEC:
        return payload
    fresh = _build_payload()
    _cache["payload"] = fresh
    _cache["ts"] = now
    return fresh


def _invalidate_cache() -> None:
    """Сбросить TTL-cache (используется в тестах)."""
    _cache["ts"] = 0.0
    _cache["payload"] = None


def build_commands_admin_router(ctx: RouterContext) -> APIRouter:  # noqa: ARG001
    """Factory: возвращает APIRouter с командной admin-консолью."""
    router = APIRouter(tags=["commands-admin"])

    # ── GET /api/admin/commands/list ────────────────────────────────────────

    @router.get("/api/admin/commands/list")
    async def commands_list() -> dict:
        """Список всех команд с метаданными + usage stats.

        Возвращает {ok, ts, commands[...], summary, categories,
        custom_aliases_total}.
        """
        return _cached_payload()

    # ── GET /api/admin/commands/usage_summary ───────────────────────────────

    @router.get("/api/admin/commands/usage_summary")
    async def commands_usage_summary() -> dict:
        """Краткая аналитика: top-10 + never_used + totals."""
        payload = _cached_payload()
        summary = payload["summary"]
        return {
            "ok": True,
            "ts": payload["ts"],
            "total_commands": summary["total_commands"],
            "total_invocations": summary["total_invocations"],
            "unique_commands_used": summary["unique_commands_used"],
            "never_used_count": summary["never_used_count"],
            "recent_used_count": summary["recent_used_count"],
            "recent_window_days": summary["recent_window_days"],
            "owner_only_count": summary["owner_only_count"],
            "by_category": summary["by_category"],
            "top_10": summary["top_10"],
            "never_used": summary["never_used"],
        }

    # ── GET /admin/commands — HTML ───────────────────────────────────────────

    @router.get("/admin/commands", response_class=HTMLResponse)
    async def commands_admin_page() -> HTMLResponse:
        """HTML index: search + group-by-category + summary stats."""
        return HTMLResponse(_COMMANDS_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/commands ────────────────────────────────────────────
# Все user/registry значения проставляются через textContent или DOM API
# (createElement) — никаких innerHTML с динамическими данными. XSS-safe.

_COMMANDS_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Admin Commands</title>
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
            --danger: #ef4444;
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

        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        .summary-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 14px;
        }
        .summary-card .label {
            color: var(--text-muted);
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .summary-card .value {
            font-size: 1.4rem;
            font-weight: 700;
            margin-top: 4px;
        }
        .summary-card .value.accent { color: var(--accent); }
        .summary-card .value.warn   { color: var(--warn);   }

        .controls {
            display: flex; gap: 12px; align-items: center;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
            margin-bottom: 18px;
        }
        .controls input[type="text"] {
            flex: 1;
            background: #0a0a0a;
            border: 1px solid var(--border);
            border-radius: 4px;
            color: var(--text);
            padding: 6px 10px;
            font-size: 0.92rem;
            outline: none;
        }
        .controls input[type="text"]:focus { border-color: var(--accent-strong); }
        .controls select {
            background: #0a0a0a; color: var(--text);
            border: 1px solid var(--border); border-radius: 4px;
            padding: 6px 10px; font-size: 0.88rem;
        }
        .controls .count {
            color: var(--text-muted); font-size: 0.85rem;
        }

        h2.section {
            font-size: 1.05rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin: 24px 0 8px;
        }
        .top-list, .never-list {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
            margin-bottom: 14px;
            font-size: 0.88rem;
        }
        .top-list ol { margin: 4px 0; padding-left: 22px; }
        .top-list li { padding: 2px 0; }
        .top-list .count {
            color: var(--accent); font-weight: 600; margin-right: 6px;
        }
        .never-list .wrap {
            display: flex; flex-wrap: wrap; gap: 4px 8px;
            margin-top: 6px;
        }
        .never-list .pill {
            font-size: 0.75rem;
            background: rgba(136,136,136,0.12);
            color: var(--text-muted);
            padding: 2px 8px;
            border-radius: 999px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        }

        .cat-group { margin-bottom: 18px; }
        .cat-header {
            display: flex; justify-content: space-between;
            align-items: center;
            background: #1a1a1a;
            padding: 8px 14px;
            border: 1px solid var(--border);
            border-radius: 6px 6px 0 0;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--accent);
        }
        .cat-header .right { color: var(--text-muted); font-weight: 400; }
        table.cmd-table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-top: none;
            border-radius: 0 0 6px 6px;
            overflow: hidden;
            font-size: 0.88rem;
        }
        table.cmd-table th, table.cmd-table td {
            padding: 8px 12px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        table.cmd-table th {
            background: #161616; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.7rem;
            letter-spacing: 0.05em;
        }
        table.cmd-table tr:last-child td { border-bottom: none; }
        table.cmd-table tr.row:hover { background: rgba(125,211,252,0.04); }
        table.cmd-table .name {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            color: var(--accent);
            font-weight: 600;
        }
        table.cmd-table .alias {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            color: var(--text-muted);
            font-size: 0.78rem;
            margin-left: 6px;
        }
        table.cmd-table .desc { color: var(--text); }
        table.cmd-table .desc .last {
            color: var(--text-muted);
            font-size: 0.78rem;
            display: block;
            margin-top: 3px;
        }
        .badge {
            display: inline-block; padding: 2px 7px;
            border-radius: 3px; font-size: 0.7rem; font-weight: 500;
            margin-right: 4px;
        }
        .badge.owner { background: rgba(239,68,68,0.15); color: var(--danger); }
        .badge.stage-beta { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge.stage-experimental { background: rgba(239,68,68,0.18); color: var(--danger); }
        .badge.count-cell { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge.count-zero { background: rgba(136,136,136,0.12); color: var(--text-muted); }

        .err-banner {
            color: var(--danger);
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
    </style>
</head>
<body>
    <header>
        <h1>⚡ Krab · Admin Commands</h1>
        <div class="meta">Wave 190 · polling 30s</div>
    </header>
    <main>
        <div id="err-banner"></div>

        <div class="summary-grid" id="summary-grid"></div>

        <div class="controls">
            <input type="text" id="search-input"
                placeholder="Search by name or description… (e.g. swarm, voice, !ask)">
            <select id="cat-filter"><option value="">All categories</option></select>
            <select id="owner-filter">
                <option value="">All access</option>
                <option value="owner">Owner-only</option>
                <option value="public">Public</option>
            </select>
            <span class="count" id="visible-count">—</span>
        </div>

        <h2 class="section">Top 10 most used (last reset)</h2>
        <div class="top-list" id="top-list">Загрузка…</div>

        <h2 class="section">Never used</h2>
        <div class="never-list" id="never-list">Загрузка…</div>

        <h2 class="section">All commands by category</h2>
        <div id="categories-container"></div>

        <footer>
            Krab Admin Console · Wave 190 · <span id="footer-ts">—</span>
        </footer>
    </main>
    <script>
        const STATE = { data: null, search: "", cat: "", owner: "" };

        function mkText(tag, text, cls) {
            const el = document.createElement(tag);
            if (cls) el.className = cls;
            if (text !== undefined && text !== null) el.textContent = String(text);
            return el;
        }

        function formatTs(ts) {
            if (!ts) return "—";
            try {
                const d = new Date(ts * 1000);
                return d.toLocaleString("ru-RU", { hour12: false });
            } catch (e) { return "—"; }
        }

        function buildSummary(summary) {
            const grid = document.getElementById("summary-grid");
            while (grid.firstChild) grid.removeChild(grid.firstChild);
            const items = [
                { label: "Total commands", value: summary.total_commands,
                  cls: "accent" },
                { label: "Total invocations",
                  value: summary.total_invocations, cls: "accent" },
                { label: "Unique used",
                  value: summary.unique_commands_used },
                { label: "Never used",
                  value: summary.never_used_count, cls: "warn" },
                { label: "Owner-only",
                  value: summary.owner_only_count },
                { label: "Used last " + summary.recent_window_days + "d",
                  value: summary.recent_used_count, cls: "accent" },
            ];
            items.forEach(function(it) {
                const card = document.createElement("div");
                card.className = "summary-card";
                card.appendChild(mkText("div", it.label, "label"));
                card.appendChild(mkText("div", it.value,
                    "value" + (it.cls ? " " + it.cls : "")));
                grid.appendChild(card);
            });
        }

        function buildTopList(top10) {
            const box = document.getElementById("top-list");
            while (box.firstChild) box.removeChild(box.firstChild);
            if (!top10 || !top10.length) {
                box.appendChild(mkText("div",
                    "(нет данных об использовании)"));
                return;
            }
            const ol = document.createElement("ol");
            top10.forEach(function(c) {
                const li = document.createElement("li");
                li.appendChild(mkText("span",
                    c.usage_count, "count mono"));
                li.appendChild(mkText("span", "!" + c.name, "mono"));
                li.appendChild(document.createTextNode(
                    " (" + c.category + ", last: "
                    + formatTs(c.last_used_ts) + ")"));
                ol.appendChild(li);
            });
            box.appendChild(ol);
        }

        function buildNeverList(neverUsed) {
            const box = document.getElementById("never-list");
            while (box.firstChild) box.removeChild(box.firstChild);
            if (!neverUsed || !neverUsed.length) {
                box.appendChild(mkText("div", "Все команды использовались."));
                return;
            }
            const header = mkText("div",
                neverUsed.length + " команд не вызывались ни разу:");
            box.appendChild(header);
            const wrap = document.createElement("div");
            wrap.className = "wrap";
            neverUsed.forEach(function(name) {
                wrap.appendChild(mkText("span", "!" + name, "pill"));
            });
            box.appendChild(wrap);
        }

        function buildCategoryFilter(categories) {
            const sel = document.getElementById("cat-filter");
            while (sel.children.length > 1) sel.removeChild(sel.lastChild);
            categories.forEach(function(c) {
                const opt = document.createElement("option");
                opt.value = c.key;
                opt.textContent = c.label + " (" + c.count + ")";
                sel.appendChild(opt);
            });
        }

        function matchesFilters(cmd) {
            if (STATE.cat && cmd.category !== STATE.cat) return false;
            if (STATE.owner === "owner" && !cmd.owner_only) return false;
            if (STATE.owner === "public" && cmd.owner_only) return false;
            if (STATE.search) {
                const q = STATE.search.toLowerCase();
                const hay = (cmd.name + " " + cmd.description + " "
                    + (cmd.aliases || []).join(" ")).toLowerCase();
                if (hay.indexOf(q) < 0) return false;
            }
            return true;
        }

        function renderCommandRow(cmd) {
            const tr = document.createElement("tr");
            tr.className = "row";

            // Name + aliases.
            const tdName = document.createElement("td");
            tdName.appendChild(mkText("span", "!" + cmd.name, "name"));
            const allAliases = (cmd.aliases || []).concat(
                cmd.custom_aliases || []);
            if (allAliases.length) {
                tdName.appendChild(mkText("span",
                    " · " + allAliases.map(function(a) {
                        return "!" + a;
                    }).join(" "), "alias"));
            }
            // Owner badge under name.
            const badgeRow = document.createElement("div");
            badgeRow.style.marginTop = "3px";
            if (cmd.owner_only) {
                badgeRow.appendChild(mkText("span", "owner", "badge owner"));
            }
            if (cmd.stage && cmd.stage !== "production") {
                badgeRow.appendChild(mkText("span", cmd.stage,
                    "badge stage-" + cmd.stage));
            }
            if (badgeRow.children.length) tdName.appendChild(badgeRow);
            tr.appendChild(tdName);

            // Description.
            const tdDesc = document.createElement("td");
            tdDesc.className = "desc";
            tdDesc.appendChild(mkText("div", cmd.description || "—"));
            if (cmd.usage) {
                const usage = mkText("div", cmd.usage, "last mono");
                tdDesc.appendChild(usage);
            }
            tr.appendChild(tdDesc);

            // Count.
            const tdCount = document.createElement("td");
            tdCount.style.textAlign = "right";
            tdCount.style.whiteSpace = "nowrap";
            const cls = cmd.usage_count > 0 ? "count-cell" : "count-zero";
            tdCount.appendChild(mkText("span", cmd.usage_count, "badge " + cls));
            const lastDiv = mkText("div", formatTs(cmd.last_used_ts), "last mono");
            lastDiv.style.color = "var(--text-muted)";
            lastDiv.style.fontSize = "0.72rem";
            lastDiv.style.marginTop = "3px";
            tdCount.appendChild(lastDiv);
            tr.appendChild(tdCount);

            return tr;
        }

        function renderCategories() {
            const container = document.getElementById("categories-container");
            while (container.firstChild) container.removeChild(container.firstChild);
            if (!STATE.data) return;

            const visible = STATE.data.commands.filter(matchesFilters);
            document.getElementById("visible-count").textContent =
                visible.length + " / " + STATE.data.commands.length;

            // Group visible by category preserving order.
            const groups = {};
            const order = [];
            visible.forEach(function(c) {
                if (!groups[c.category]) {
                    groups[c.category] = [];
                    order.push(c.category);
                }
                groups[c.category].push(c);
            });

            const labels = {};
            (STATE.data.categories || []).forEach(function(c) {
                labels[c.key] = c.label;
            });

            order.forEach(function(catKey) {
                const cmds = groups[catKey];
                const wrap = document.createElement("div");
                wrap.className = "cat-group";

                const head = document.createElement("div");
                head.className = "cat-header";
                head.appendChild(mkText("span",
                    labels[catKey] || ("· " + catKey)));
                head.appendChild(mkText("span",
                    cmds.length + " команд", "right"));
                wrap.appendChild(head);

                const table = document.createElement("table");
                table.className = "cmd-table";
                const thead = document.createElement("thead");
                const headRow = document.createElement("tr");
                ["Command", "Description", "Usage"].forEach(function(t) {
                    headRow.appendChild(mkText("th", t));
                });
                thead.appendChild(headRow);
                table.appendChild(thead);
                const tbody = document.createElement("tbody");
                cmds.forEach(function(c) {
                    tbody.appendChild(renderCommandRow(c));
                });
                table.appendChild(tbody);
                wrap.appendChild(table);
                container.appendChild(wrap);
            });
        }

        async function fetchData() {
            const errBanner = document.getElementById("err-banner");
            errBanner.textContent = "";
            try {
                const res = await fetch("/api/admin/commands/list");
                if (!res.ok) throw new Error("HTTP " + res.status);
                const data = await res.json();
                STATE.data = data;
                buildSummary(data.summary);
                buildTopList(data.summary.top_10);
                buildNeverList(data.summary.never_used);
                buildCategoryFilter(data.categories || []);
                renderCategories();
                document.getElementById("footer-ts").textContent =
                    formatTs(data.ts);
            } catch (e) {
                const banner = document.createElement("div");
                banner.className = "err-banner";
                banner.textContent = "Ошибка загрузки: " + e.message;
                errBanner.appendChild(banner);
            }
        }

        document.getElementById("search-input").addEventListener("input",
            function(e) {
                STATE.search = e.target.value || "";
                renderCategories();
            });
        document.getElementById("cat-filter").addEventListener("change",
            function(e) {
                STATE.cat = e.target.value || "";
                renderCategories();
            });
        document.getElementById("owner-filter").addEventListener("change",
            function(e) {
                STATE.owner = e.target.value || "";
                renderCategories();
            });

        fetchData();
        setInterval(fetchData, 30000);
    </script>
</body>
</html>
"""
