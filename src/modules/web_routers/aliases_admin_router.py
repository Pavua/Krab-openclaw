# -*- coding: utf-8 -*-
"""
Aliases admin router — Wave 200 (Session 48).

Owner-side панель для управления пользовательскими алиасами команд Krab.
Алиасы хранятся в ``~/.openclaw/krab_runtime_state/command_aliases.json``
(см. ``src.core.command_aliases``). Каждый алиас связывает короткое имя
с целевой командой (опционально с inline-аргументами).

Endpoints (READY):
- GET  /api/admin/aliases/list     — JSON список алиасов + available commands.
- POST /api/admin/aliases/add      — write-access, body {name, target, args?}.
- POST /api/admin/aliases/remove   — write-access, body {name}.
- GET  /admin/aliases               — HTML страница с инлайн-формой.

Контракт безопасности: write эндпоинты проходят через
``ctx.assert_write_access_fn``. Имя алиаса валидируется regex
``^[a-z][a-z0-9_-]{1,32}$`` (без ведущих цифр, без пробелов), также
проверяется коллизия с зарегистрированными командами.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.command_aliases import RESERVED_NAMES, alias_service
from src.core.command_registry import registry as command_registry
from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Путь к файлу со счётчиками использования команд (Wave 38, core/command_registry).
_USAGE_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "command_usage.json"

# Валидация имени алиаса:
#  • первый символ — буква нижнего регистра;
#  • далее буквы/цифры/_/-;
#  • общий размер 2..33 символа.
_ALIAS_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")

# Максимальная длина опциональных args-аргументов (inline для resolve()).
_MAX_ARGS_LEN = 256


# ── helpers ─────────────────────────────────────────────────────────────────


def _normalize_alias_name(raw: str) -> str:
    """Приводит имя к нижнему регистру + strip '!'/'/'/'.' префиксов."""
    name = (raw or "").strip().lstrip("!/.").lower()
    return name


def _validate_alias_name(raw: str) -> str:
    """Sanitize/validate alias имя. Возвращает нормализованное имя."""
    name = _normalize_alias_name(raw)
    if not name:
        raise HTTPException(status_code=400, detail="alias_name_empty")
    if not _ALIAS_NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="alias_name_invalid_format")
    if name in RESERVED_NAMES:
        raise HTTPException(status_code=400, detail=f"alias_name_reserved:{name}")
    return name


def _available_command_names() -> list[str]:
    """Возвращает отсортированный список имён всех зарегистрированных команд."""
    return sorted({cmd.name for cmd in command_registry.all()})


def _is_known_command(name: str) -> bool:
    """True если ``name`` соответствует зарегистрированной команде (или её алиасу)."""
    return command_registry.get(name) is not None


def _load_usage_counts() -> dict[str, int]:
    """Читает ``command_usage.json`` → {command_name: count}. Любая ошибка → {}.

    Поддерживает legacy (плоский dict) и новый формат ({counts, last_ts}).
    """
    try:
        if not _USAGE_FILE.exists():
            return {}
        raw = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("aliases_admin.usage_read_failed", error=str(exc))
        return {}
    if not isinstance(raw, dict):
        return {}
    if "counts" in raw and isinstance(raw["counts"], dict):
        return {str(k): int(v) for k, v in raw["counts"].items() if isinstance(v, (int, float))}
    # Legacy: плоский {name: count}.
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}


def _split_alias_value(value: str) -> tuple[str, str]:
    """Разбивает сохранённое значение алиаса на (target, args).

    Значения в command_aliases.json хранятся как строка вида ``"translate auto"``
    — без '!' префикса. Первый токен — target, всё после — args.
    """
    parts = (value or "").strip().split(None, 1)
    if not parts:
        return "", ""
    target = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    return target, args


def _build_alias_entry(
    name: str,
    value: str,
    usage_counts: dict[str, int],
) -> dict[str, Any]:
    """Формирует один элемент списка алиасов для API."""
    target, args = _split_alias_value(value)
    return {
        "name": name,
        "target": target,
        "args": args,
        # Конфликт = имя алиаса совпадает с встроенной командой/её алиасом.
        "conflicts": _is_known_command(name),
        # Усиление: target должен существовать, иначе алиас "битый".
        "target_known": _is_known_command(target),
        # usage_count считается по алиасному имени (bump_command вызывается
        # после resolve по реальной команде, но мы также показываем счётчик
        # самого алиаса если он попал в registry usage).
        "usage_count": int(usage_counts.get(name, 0)),
    }


# ── factory ─────────────────────────────────────────────────────────────────


def build_aliases_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с endpoints управления алиасами команд."""
    router = APIRouter(tags=["aliases-admin"])

    # ── GET /api/admin/aliases/list ─────────────────────────────────────────

    @router.get("/api/admin/aliases/list")
    async def aliases_list() -> dict:
        """JSON список алиасов + список зарегистрированных команд для dropdown."""
        try:
            aliases_map = alias_service.list_all()
        except Exception as exc:  # noqa: BLE001
            _logger.error("aliases_admin.list_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"aliases_list_failed: {exc}",
            ) from exc

        usage_counts = _load_usage_counts()
        aliases_payload = [
            _build_alias_entry(name, value, usage_counts)
            for name, value in sorted(aliases_map.items())
        ]
        commands = _available_command_names()
        # Подсчёт конфликтов в payload — для удобства UI summary.
        conflict_count = sum(1 for entry in aliases_payload if entry["conflicts"])

        return {
            "ok": True,
            "count": len(aliases_payload),
            "conflict_count": conflict_count,
            "aliases": aliases_payload,
            "available_commands": commands,
        }

    # ── POST /api/admin/aliases/add ─────────────────────────────────────────

    @router.post("/api/admin/aliases/add")
    async def aliases_add(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создаёт/обновляет алиас. Body: ``{name, target, args?}``."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_name = str(payload.get("name") or "").strip()
        raw_target = str(payload.get("target") or "").strip().lstrip("!/.")
        raw_args = str(payload.get("args") or "").strip()

        name = _validate_alias_name(raw_name)

        if not raw_target:
            raise HTTPException(status_code=400, detail="alias_target_empty")
        if len(raw_args) > _MAX_ARGS_LEN:
            raise HTTPException(status_code=400, detail="alias_args_too_long")

        # target должен быть известной командой.
        target_name = raw_target.split(None, 1)[0].lower()
        if not _is_known_command(target_name):
            raise HTTPException(
                status_code=400,
                detail=f"alias_target_unknown:{target_name}",
            )

        # Запрет: имя алиаса совпадает с зарегистрированной командой
        # (даже после прохождения regex/RESERVED_NAMES — командой может
        # стать что-то новое, например 'archive', 'cron').
        if _is_known_command(name):
            raise HTTPException(
                status_code=409,
                detail=f"alias_name_collides_with_command:{name}",
            )

        # Собираем хранимое значение: "target args".
        stored_value = target_name if not raw_args else f"{target_name} {raw_args}"
        ok, message = alias_service.add(name, stored_value)
        if not ok:
            # alias_service.add возвращает (False, msg) при превышении лимита
            # или прочих edge cases — отдаём 400 наружу.
            raise HTTPException(status_code=400, detail=message)
        _logger.info(
            "aliases_admin.add",
            name=name,
            target=target_name,
            args_len=len(raw_args),
        )
        return {
            "ok": True,
            "name": name,
            "target": target_name,
            "args": raw_args,
            "message": message,
        }

    # ── POST /api/admin/aliases/remove ──────────────────────────────────────

    @router.post("/api/admin/aliases/remove")
    async def aliases_remove(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Удаляет алиас. Body: ``{name}``."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_name = str(payload.get("name") or "").strip()
        name = _normalize_alias_name(raw_name)
        if not name:
            raise HTTPException(status_code=400, detail="alias_name_empty")

        ok, message = alias_service.remove(name)
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        _logger.info("aliases_admin.remove", name=name)
        return {"ok": True, "name": name, "message": message}

    # ── GET /admin/aliases — HTML ───────────────────────────────────────────

    @router.get("/admin/aliases", response_class=HTMLResponse)
    async def aliases_admin_page() -> HTMLResponse:
        """HTML страница с inline add-формой + таблицей алиасов."""
        return HTMLResponse(_ALIASES_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/aliases ────────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — никаких
# innerHTML с внешними строками (XSS-safe).

_ALIASES_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Aliases Admin</title>
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
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 16px;
        }
        .card h2 { margin: 0 0 10px 0; font-size: 1rem; color: var(--accent); }
        .form-row {
            display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
        }
        input[type="text"], select {
            background: #0a0a0a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 6px 10px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 0.9rem;
        }
        input[type="text"] { min-width: 140px; }
        select { min-width: 180px; }
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
        tr:hover { background: rgba(125, 211, 252, 0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 5px 12px;
            font-size: 0.8rem;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.danger { border-color: var(--err); color: var(--err); background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner {
            color: var(--err); padding: 8px 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px;
            margin-bottom: 12px; font-size: 0.85rem;
        }
        .hint { color: var(--text-muted); font-size: 0.75rem; margin-top: 6px; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Aliases Admin</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div class="card">
            <h2>Добавить алиас</h2>
            <div class="form-row">
                <input type="text" id="new-name" placeholder="имя (alias)" maxlength="33">
                <select id="new-target"><option value="">— команда —</option></select>
                <input type="text" id="new-args" placeholder="args (опц.)" maxlength="256">
                <button id="btn-add">Создать</button>
            </div>
            <div class="hint">
                Имя: только нижний регистр, буквы/цифры/_/-, начинается с буквы. 2..33 символов.
            </div>
            <div id="form-err" class="err-banner" style="display:none; margin-top: 8px;"></div>
        </div>

        <div id="summary" class="summary">Загружаем алиасы…</div>
        <div id="list-err" class="err-banner" style="display:none;"></div>
        <table id="aliases-table">
            <thead>
                <tr>
                    <th>Имя</th>
                    <th>Target</th>
                    <th>Args</th>
                    <th>Usage</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="aliases-body"></tbody>
        </table>
    </main>
    <script>
        async function callAdmin(method, url, body) {
            const opts = { method: method };
            if (body !== undefined) {
                opts.headers = { 'Content-Type': 'application/json' };
                opts.body = JSON.stringify(body);
            }
            const res = await fetch(url, opts);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const err = new Error(data.detail || ('HTTP ' + res.status));
                err.detail = data.detail;
                throw err;
            }
            return data;
        }
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function mkButton(text, onClick, cls) {
            const btn = document.createElement('button');
            btn.textContent = text;
            if (cls) btn.className = cls;
            btn.addEventListener('click', onClick);
            return btn;
        }
        function mkCell(content) {
            const td = document.createElement('td');
            if (typeof content === 'string') td.textContent = content;
            else if (content instanceof Node) td.appendChild(content);
            return td;
        }
        function mkMonoCell(text) {
            const td = document.createElement('td');
            td.className = 'mono';
            td.textContent = text;
            return td;
        }
        async function removeAlias(name) {
            if (!confirm('Удалить алиас !' + name + '?')) return;
            try {
                await callAdmin('POST', '/api/admin/aliases/remove', { name: name });
                fetchAliases();
            } catch (e) {
                alert('Не удалось удалить: ' + e.message);
            }
        }
        async function addAlias() {
            const nameInput = document.getElementById('new-name');
            const targetSel = document.getElementById('new-target');
            const argsInput = document.getElementById('new-args');
            const errBanner = document.getElementById('form-err');
            errBanner.style.display = 'none';
            errBanner.textContent = '';
            const name = (nameInput.value || '').trim();
            const target = (targetSel.value || '').trim();
            const args = (argsInput.value || '').trim();
            if (!name || !target) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Имя и target обязательны.';
                return;
            }
            try {
                await callAdmin('POST', '/api/admin/aliases/add',
                    { name: name, target: target, args: args });
                nameInput.value = '';
                argsInput.value = '';
                targetSel.value = '';
                fetchAliases();
            } catch (e) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Ошибка: ' + e.message;
            }
        }
        function renderStatusCell(entry) {
            const td = document.createElement('td');
            if (entry.conflicts) {
                td.appendChild(mkBadge('conflict', 'badge-err'));
                td.appendChild(document.createTextNode(' '));
            }
            if (!entry.target_known) {
                td.appendChild(mkBadge('target?', 'badge-warn'));
                td.appendChild(document.createTextNode(' '));
            }
            if (!entry.conflicts && entry.target_known) {
                td.appendChild(mkBadge('ok', 'badge-ok'));
            }
            return td;
        }
        function renderActionsCell(name) {
            const td = document.createElement('td');
            td.appendChild(mkButton('🗑 remove', () => removeAlias(name), 'danger'));
            return td;
        }
        function populateCommands(commands) {
            const sel = document.getElementById('new-target');
            const currentVal = sel.value;
            while (sel.options.length > 1) sel.remove(1);
            for (const name of commands) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = '!' + name;
                sel.appendChild(opt);
            }
            sel.value = currentVal;
        }
        async function fetchAliases() {
            const errBanner = document.getElementById('list-err');
            errBanner.style.display = 'none';
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/aliases/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const aliases = data.aliases || [];
                const commands = data.available_commands || [];
                populateCommands(commands);
                const tbody = document.getElementById('aliases-body');
                while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
                for (const a of aliases) {
                    const tr = document.createElement('tr');
                    tr.appendChild(mkMonoCell('!' + a.name));
                    tr.appendChild(mkMonoCell('!' + (a.target || '')));
                    tr.appendChild(mkMonoCell(a.args || '—'));
                    tr.appendChild(mkCell(String(a.usage_count || 0)));
                    tr.appendChild(renderStatusCell(a));
                    tr.appendChild(renderActionsCell(a.name));
                    tbody.appendChild(tr);
                }
                const summary = document.getElementById('summary');
                summary.textContent = 'Всего алиасов: ' + aliases.length +
                    ' · конфликтов: ' + (data.conflict_count || 0) +
                    ' · команд в реестре: ' + commands.length;
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Ошибка загрузки: ' + e.message;
            }
        }
        document.getElementById('btn-add').addEventListener('click', addAlias);
        fetchAliases();
        setInterval(fetchAliases, 30000);
    </script>
</body>
</html>
"""
