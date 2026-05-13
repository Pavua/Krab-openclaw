# -*- coding: utf-8 -*-
"""
Skills admin router — Wave 198 (Session 48).

Read-only owner-side панель для модулей пакета ``src/skills/``. Сканирует
исходные ``.py`` файлы (без импорта — чтобы не подтягивать тяжёлые
зависимости вроде Playwright/httpx в процесс owner-panel), извлекает
metadata через статический парсинг (имя, путь, LOC, public-функции,
mtime, docstring).

Дополнительно подтягивает ``skill_curator`` daily-JSON отчёты из
``~/.openclaw/krab_runtime_state/skill_curator/daily/<team>/*.json``
(Wave 14-I, Session 40). Эндпоинты возвращают сводку без раскрытия
содержимого reports — только высокоуровневые поля (rounds/success_rate
и т.п.).

Endpoints (READ-ONLY):
- GET /api/admin/skills/list                          — JSON list of skills.
- GET /api/admin/skills/{name}/curator_reports        — JSON list reports.
- GET /admin/skills                                    — HTML page (cards).

ВНИМАНИЕ: страница НЕ wired в landing/web_app до commit'а owner-mediator.
Router сам по себе живой если включить через ``app.include_router(...)``,
но это делает соседний коммит. Все эндпоинты безопасны для read-only.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Каталог skills внутри репозитория. Резолвится через project_root в factory,
# но для standalone-вызовов helper'ов используем сборку относительно __file__.
_SKILLS_DIR_FALLBACK = Path(__file__).resolve().parents[3] / "src" / "skills"

# Wave 14-I: каталог с daily-отчётами skill_curator.
_CURATOR_DAILY_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "skill_curator" / "daily"

# Имя модуля → команды teams в skill_curator. Список team-каталогов
# фиксированный и совпадает с TEAM_REGISTRY (см. swarm_bus).
_CURATOR_TEAMS = ("traders", "coders", "analysts", "creative")

# Skill-имена которые НЕ нужно показывать (helper'ы пакета skills).
_HIDDEN_SKILL_NAMES = frozenset({"stealth_browser"})

# Допустимые символы в name для path-segment в URL.
_NAME_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _validate_skill_name(name: str) -> str:
    """Валидация name перед чтением reports — защита от path traversal."""
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise HTTPException(status_code=400, detail="skills_invalid_name")
    if not all(ch in _NAME_SAFE_CHARS for ch in name):
        raise HTTPException(status_code=400, detail="skills_invalid_name")
    return name


def _skills_dir(ctx: RouterContext | None = None) -> Path:
    """Возвращает Path до ``src/skills/`` исходя из ctx.project_root,
    с fallback на путь относительно текущего файла."""
    if ctx is not None:
        candidate = ctx.project_root / "src" / "skills"
        if candidate.exists():
            return candidate
    return _SKILLS_DIR_FALLBACK


def _extract_public_functions(source: str) -> list[dict[str, Any]]:
    """Парсит AST и возвращает список public функций (не начинаются с '_').

    Возвращает: [{"name": str, "is_async": bool, "lineno": int}, ...]
    """
    out: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            out.append(
                {
                    "name": node.name,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "lineno": node.lineno,
                }
            )
    return out


def _extract_module_docstring(source: str) -> str:
    """Возвращает первую строку module-docstring или пустую строку."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    # Берём первую непустую строку
    for line in doc.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _scan_skill_file(path: Path) -> dict[str, Any] | None:
    """Парсит один skill-модуль. Возвращает None если файл нечитаем."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("skills_admin.read_failed", path=str(path), error=str(exc))
        return None

    line_count = source.count("\n") + (0 if source.endswith("\n") else 1)
    public_funcs = _extract_public_functions(source)
    docstring = _extract_module_docstring(source)

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    return {
        "name": path.stem,
        "file": str(path),
        "relative_file": f"src/skills/{path.name}",
        "line_count": line_count,
        "public_functions": public_funcs,
        "public_function_count": len(public_funcs),
        "docstring": docstring,
        "mtime": mtime,
    }


def _enumerate_skills(ctx: RouterContext | None = None) -> list[dict[str, Any]]:
    """Сканирует ``src/skills/*.py``, исключает ``__init__`` и hidden helpers."""
    skills_dir = _skills_dir(ctx)
    if not skills_dir.exists():
        return []

    out: list[dict[str, Any]] = []
    for path in sorted(skills_dir.glob("*.py")):
        stem = path.stem
        if stem.startswith("__") or stem in _HIDDEN_SKILL_NAMES:
            continue
        info = _scan_skill_file(path)
        if info is not None:
            out.append(info)
    return out


def _load_curator_reports_for_skill(name: str) -> list[dict[str, Any]]:
    """Возвращает daily-отчёты для skill (matched по team-имени).

    Маппинг: если ``name`` совпадает с одной из team — берём её каталог.
    Иначе — возвращаем сводку по всем 4 командам (это семантически
    skill-curator работает per-team, не per-skill).
    """
    reports: list[dict[str, Any]] = []
    if not _CURATOR_DAILY_DIR.exists():
        return reports

    teams = (name,) if name in _CURATOR_TEAMS else _CURATOR_TEAMS
    for team in teams:
        team_dir = _CURATOR_DAILY_DIR / team
        if not team_dir.exists():
            continue
        for json_path in sorted(team_dir.glob("*.json"), reverse=True):
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                _logger.warning(
                    "skills_admin.curator_read_failed",
                    path=str(json_path),
                    error=str(exc),
                )
                continue
            if not isinstance(payload, dict):
                continue
            reports.append(
                {
                    "team": team,
                    "date": payload.get("date") or json_path.stem,
                    "rounds_analyzed": payload.get("rounds_analyzed", 0),
                    "success_rate": payload.get("success_rate", 0.0),
                    "distinct_topics": payload.get("distinct_topics", 0),
                    "recurring_failure_tags": payload.get("recurring_failure_tags") or [],
                    "generated_at": payload.get("generated_at", ""),
                    "file": str(json_path),
                }
            )
    return reports


def build_skills_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с read-only endpoints для /admin/skills."""
    router = APIRouter(tags=["skills-admin"])

    # ── GET /api/admin/skills/list ──────────────────────────────────────────

    @router.get("/api/admin/skills/list")
    async def skills_list() -> dict:
        """Возвращает список skill-модулей с metadata.

        Поля per skill: name, file, relative_file, line_count,
        public_functions[], public_function_count, docstring (1st line),
        mtime (unix-epoch).
        """
        try:
            skills = _enumerate_skills(ctx)
        except Exception as exc:  # noqa: BLE001
            _logger.error("skills_admin.list_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"skills_list_failed: {exc}") from exc
        return {"ok": True, "count": len(skills), "skills": skills}

    # ── GET /api/admin/skills/{name}/curator_reports ────────────────────────

    @router.get("/api/admin/skills/{name}/curator_reports")
    async def skills_curator_reports(name: str) -> dict:
        """Возвращает daily-отчёты skill_curator (Wave 14-I).

        Если ``name`` совпадает с командой свёрма (traders/coders/analysts/
        creative) — отдаёт отчёты только этой команды. Иначе — сводный
        список по всем командам (отсортированный по дате DESC).
        """
        name = _validate_skill_name(name)
        try:
            reports = _load_curator_reports_for_skill(name)
        except Exception as exc:  # noqa: BLE001
            _logger.error("skills_admin.curator_reports_failed", name=name, error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"skills_curator_reports_failed: {exc}",
            ) from exc
        return {"ok": True, "name": name, "count": len(reports), "reports": reports}

    # ── GET /admin/skills — HTML page ───────────────────────────────────────

    @router.get("/admin/skills", response_class=HTMLResponse)
    async def skills_admin_page() -> HTMLResponse:
        """HTML страница со списком skill-модулей (cards layout)."""
        return HTMLResponse(_SKILLS_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/skills ──────────────────────────────────────────────
# Vanilla JS, DOM-построение через createElement/textContent (XSS-safe).

_SKILLS_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Skills Admin</title>
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
        .summary { color: var(--text-muted); margin-bottom: 16px; font-size: 0.9rem; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
            gap: 16px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px 16px;
            display: flex; flex-direction: column; gap: 8px;
        }
        .card h2 {
            margin: 0; font-size: 1.05rem; color: var(--accent);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        }
        .card .doc { color: var(--text-muted); font-size: 0.85rem; min-height: 1.3em; }
        .card .meta-row {
            display: flex; flex-wrap: wrap; gap: 6px 12px;
            font-size: 0.8rem; color: var(--text-muted);
        }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
            background: rgba(125,211,252,0.1); color: var(--accent);
        }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        .func-list {
            margin: 4px 0 0 0; padding: 0; list-style: none;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
            font-size: 0.8rem; color: var(--text);
            max-height: 140px; overflow-y: auto;
        }
        .func-list li { padding: 2px 0; border-bottom: 1px dashed rgba(255,255,255,0.04); }
        .func-list li:last-child { border-bottom: none; }
        .async-tag { color: var(--warn); font-size: 0.7rem; margin-left: 4px; }
        .err-banner {
            color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px;
            margin-bottom: 12px;
        }
        details summary {
            cursor: pointer; color: var(--text-muted); font-size: 0.8rem;
            margin-top: 4px;
        }
        details summary:hover { color: var(--accent); }
        .reports-box {
            margin-top: 8px; font-size: 0.78rem; color: var(--text-muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
        }
        .reports-box .row { padding: 2px 0; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Skills Admin</h1>
        <div class="meta">Read-only · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем skills…</div>
        <div id="err-banner"></div>
        <div id="skills-grid" class="grid"></div>
    </main>
    <script>
        function fmtAge(ts) {
            if (!ts) return '—';
            try {
                const ageSec = Math.floor(Date.now() / 1000 - ts);
                if (ageSec < 60) return ageSec + 's ago';
                if (ageSec < 3600) return Math.floor(ageSec / 60) + 'm ago';
                if (ageSec < 86400) return Math.floor(ageSec / 3600) + 'h ago';
                return Math.floor(ageSec / 86400) + 'd ago';
            } catch (e) { return '—'; }
        }
        function mkEl(tag, opts) {
            const el = document.createElement(tag);
            if (!opts) return el;
            if (opts.cls) el.className = opts.cls;
            if (opts.text !== undefined) el.textContent = opts.text;
            if (opts.title) el.title = opts.title;
            return el;
        }
        function mkBadge(text, cls) {
            return mkEl('span', { cls: 'badge ' + (cls || ''), text: text });
        }
        async function loadCuratorReports(name, container) {
            try {
                const res = await fetch(
                    '/api/admin/skills/' + encodeURIComponent(name) + '/curator_reports'
                );
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                while (container.firstChild) container.removeChild(container.firstChild);
                const reports = data.reports || [];
                if (reports.length === 0) {
                    container.appendChild(mkEl('div', {
                        cls: 'row', text: 'Нет отчётов skill_curator.'
                    }));
                    return;
                }
                const shown = reports.slice(0, 8);
                for (const r of shown) {
                    const line =
                        '[' + (r.team || '?') + '] ' +
                        (r.date || '?') + ' · rounds=' + (r.rounds_analyzed || 0) +
                        ' · success=' + (r.success_rate || 0);
                    container.appendChild(mkEl('div', { cls: 'row', text: line }));
                }
                if (reports.length > shown.length) {
                    container.appendChild(mkEl('div', {
                        cls: 'row',
                        text: '… +' + (reports.length - shown.length) + ' ещё'
                    }));
                }
            } catch (e) {
                while (container.firstChild) container.removeChild(container.firstChild);
                container.appendChild(mkEl('div', {
                    cls: 'row', text: 'Ошибка: ' + e.message
                }));
            }
        }
        function renderCard(skill) {
            const card = mkEl('div', { cls: 'card' });
            card.appendChild(mkEl('h2', { text: skill.name }));
            card.appendChild(mkEl('div', {
                cls: 'doc',
                text: skill.docstring || '(нет docstring)'
            }));

            const metaRow = mkEl('div', { cls: 'meta-row' });
            metaRow.appendChild(mkBadge(
                (skill.line_count || 0) + ' LOC', 'badge-muted'
            ));
            metaRow.appendChild(mkBadge(
                'funcs: ' + (skill.public_function_count || 0), 'badge-muted'
            ));
            metaRow.appendChild(mkBadge(
                'mtime: ' + fmtAge(skill.mtime), 'badge-muted'
            ));
            card.appendChild(metaRow);

            const pathRow = mkEl('div', { cls: 'meta-row mono' });
            pathRow.appendChild(mkEl('span', {
                text: skill.relative_file || skill.file || '?'
            }));
            card.appendChild(pathRow);

            const ul = mkEl('ul', { cls: 'func-list' });
            for (const fn of (skill.public_functions || [])) {
                const li = mkEl('li');
                li.appendChild(document.createTextNode(fn.name + '()'));
                if (fn.is_async) {
                    li.appendChild(mkEl('span', { cls: 'async-tag', text: 'async' }));
                }
                ul.appendChild(li);
            }
            if ((skill.public_functions || []).length === 0) {
                const li = mkEl('li', { text: '(публичных функций нет)' });
                ul.appendChild(li);
            }
            card.appendChild(ul);

            const det = mkEl('details');
            det.appendChild(mkEl('summary', { text: 'Curator reports' }));
            const reportsBox = mkEl('div', { cls: 'reports-box' });
            reportsBox.appendChild(mkEl('div', {
                cls: 'row', text: 'Загружаем…'
            }));
            det.appendChild(reportsBox);
            det.addEventListener('toggle', () => {
                if (det.open && !det.dataset.loaded) {
                    det.dataset.loaded = '1';
                    loadCuratorReports(skill.name, reportsBox);
                }
            });
            card.appendChild(det);

            return card;
        }
        async function fetchSkills() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/skills/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const skills = data.skills || [];
                const grid = document.getElementById('skills-grid');
                while (grid.firstChild) grid.removeChild(grid.firstChild);
                for (const s of skills) {
                    grid.appendChild(renderCard(s));
                }
                const summary = document.getElementById('summary');
                while (summary.firstChild) summary.removeChild(summary.firstChild);
                summary.appendChild(document.createTextNode(
                    'Всего skills: ' + skills.length +
                    ' · суммарно функций: ' +
                    skills.reduce(
                        (a, s) => a + (s.public_function_count || 0), 0
                    )
                ));
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = mkEl('div', {
                    cls: 'err-banner', text: 'Ошибка загрузки: ' + e.message
                });
                errBanner.appendChild(banner);
            }
        }
        fetchSkills();
    </script>
</body>
</html>
"""
