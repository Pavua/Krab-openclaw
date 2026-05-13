# -*- coding: utf-8 -*-
"""
Reactions admin router — Wave 227 (Session 49).

Owner-side панель для управления авто-реакциями Краба:
  • правила (pattern → emoji + scope) хранятся в
    ``~/.openclaw/krab_runtime_state/reaction_rules.json``;
  • история последних реакций читается из
    ``~/.openclaw/krab_runtime_state/reactions_log.jsonl``;
  • статистика реакций за последние 7 дней агрегируется на лету.

Endpoints (READY):
- GET  /api/admin/reactions/list      — JSON {rules, recent, stats}.
- POST /api/admin/reactions/add       — write-access, {pattern, emoji, scope}.
- POST /api/admin/reactions/toggle/{id} — write-access, переключение enabled.
- POST /api/admin/reactions/remove/{id} — write-access, удаление правила.
- GET  /admin/reactions                — HTML страница.

Безопасность: write-эндпоинты проходят через ``ctx.assert_write_access_fn``.
Pattern проверяется как regex (re.compile), emoji валидируется по форме
(unicode-emoji или short_code ``:name:``). HTML страница рендерит превью
сообщений через ``textContent`` (никакого innerHTML) → XSS-safe.

Стиль зеркалит ``aliases_admin_router`` (Wave 200) — inline editor + таблица
+ JS polling каждые 30 секунд.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Хранилище правил. Локальное для admin-router — auto_reactions.py использует
# регекспы в коде, а правила тут — отдельный, редактируемый слой (загружается
# движком из этого же файла при наличии).
_RULES_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "reaction_rules.json"
# История событий реакций — пишется ReactionEngine в jsonl, последние N строк
# читаем для отображения.
_REACTIONS_LOG_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "reactions_log.jsonl"

# Максимум recent событий, которые показываем.
_MAX_RECENT = 50
# Сколько последних байт читать из jsonl для recent/stats (без полного чтения).
_LOG_TAIL_BYTES = 256 * 1024  # 256 KiB

# Валидация: pattern — компилируемый regex; emoji — unicode-emoji или :short_code:.
_SHORT_CODE_PATTERN = re.compile(r"^:[a-z0-9_+-]{1,32}:$")
_SCOPES = frozenset({"chat", "user", "any"})

_MAX_PATTERN_LEN = 256
_MAX_EMOJI_LEN = 32


# ── helpers (rules storage) ────────────────────────────────────────────────


def _load_rules() -> list[dict[str, Any]]:
    """Читает правила из JSON. При ошибке возвращает []."""
    try:
        if not _RULES_FILE.exists():
            return []
        raw = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("reactions_admin.rules_read_failed", error=str(exc))
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "").strip()
        pattern = str(item.get("pattern") or "")
        emoji = str(item.get("emoji") or "")
        scope = str(item.get("scope") or "any")
        if not (rid and pattern and emoji):
            continue
        out.append(
            {
                "id": rid,
                "pattern": pattern,
                "emoji": emoji,
                "scope": scope if scope in _SCOPES else "any",
                "enabled": bool(item.get("enabled", True)),
                "created_ts": float(item.get("created_ts", 0.0) or 0.0),
            }
        )
    return out


def _save_rules(rules: list[dict[str, Any]]) -> None:
    """Атомарно сохраняет правила в JSON (write-temp + rename)."""
    _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _RULES_FILE.with_suffix(_RULES_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_RULES_FILE)


def _validate_pattern(raw: str) -> str:
    """Pattern должен компилироваться как regex. Возвращает stripped."""
    pattern = (raw or "").strip()
    if not pattern:
        raise HTTPException(status_code=400, detail="reaction_pattern_empty")
    if len(pattern) > _MAX_PATTERN_LEN:
        raise HTTPException(status_code=400, detail="reaction_pattern_too_long")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(
            status_code=400,
            detail=f"reaction_pattern_invalid_regex: {exc}",
        ) from exc
    return pattern


def _is_emoji_char(ch: str) -> bool:
    """True если символ относится к emoji unicode-блокам.

    Покрывает большинство emoji + variation selectors + ZWJ.
    """
    if not ch:
        return False
    code = ord(ch)
    # Variation selector / ZWJ / skin tones / regional indicators.
    if code in (0x200D, 0xFE0F):
        return True
    if 0x1F1E6 <= code <= 0x1F1FF:  # regional indicator (flags)
        return True
    if 0x1F3FB <= code <= 0x1F3FF:  # skin tone modifiers
        return True
    # Emoji unicode property check (через category + symbol blocks).
    category = unicodedata.category(ch)
    if category in ("So", "Sk", "Sm"):
        return True
    # Часто встречающиеся emoji-плэйны:
    if 0x2600 <= code <= 0x27BF:  # misc symbols + dingbats
        return True
    if 0x1F300 <= code <= 0x1FAFF:  # supplemental symbols + emoji
        return True
    return False


def _validate_emoji(raw: str) -> str:
    """Emoji = либо unicode-emoji (≥1 emoji-char), либо ``:short_code:``."""
    emoji = (raw or "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="reaction_emoji_empty")
    if len(emoji) > _MAX_EMOJI_LEN:
        raise HTTPException(status_code=400, detail="reaction_emoji_too_long")
    # Вариант 1: short_code вида :fire:.
    if _SHORT_CODE_PATTERN.match(emoji):
        return emoji
    # Вариант 2: хотя бы один emoji-char.
    if any(_is_emoji_char(ch) for ch in emoji):
        return emoji
    raise HTTPException(status_code=400, detail="reaction_emoji_invalid_format")


def _validate_scope(raw: str) -> str:
    scope = (raw or "any").strip().lower()
    if scope not in _SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"reaction_scope_invalid: must be one of {sorted(_SCOPES)}",
        )
    return scope


# ── helpers (history + stats) ───────────────────────────────────────────────


def _tail_jsonl(path: Path, *, max_bytes: int = _LOG_TAIL_BYTES) -> list[dict[str, Any]]:
    """Читает последние ~max_bytes байт jsonl, парсит и возвращает события.

    Файл может быть пустым/отсутствовать — вернём [].
    """
    try:
        if not path.exists():
            return []
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(-max_bytes, 2)
                fh.readline()  # дропаем возможно битую первую строку
            data = fh.read()
    except OSError as exc:
        _logger.warning("reactions_admin.log_read_failed", error=str(exc))
        return []
    events: list[dict[str, Any]] = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(evt, dict):
            events.append(evt)
    return events


def _build_recent(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Формирует список последних реакций (свежие в начале, max _MAX_RECENT)."""
    # Фильтруем только reaction_added (если type есть).
    filtered = [e for e in events if e.get("type") in (None, "reaction_added", "reaction")]
    # Сортировка по ts (старые → новые), хвост × MAX, затем reverse.
    filtered.sort(key=lambda e: float(e.get("ts", 0.0) or 0.0))
    tail = filtered[-_MAX_RECENT:]
    tail.reverse()
    out: list[dict[str, Any]] = []
    for evt in tail:
        out.append(
            {
                "ts": float(evt.get("ts", 0.0) or 0.0),
                "chat_id": evt.get("chat_id"),
                "message_id": evt.get("message_id"),
                "user_id": evt.get("user_id"),
                "username": str(evt.get("username") or ""),
                "emoji": str(evt.get("emoji") or ""),
                # msg_preview опционален; обрезаем до 80 chars для UI.
                "msg_preview": str(evt.get("msg_preview") or "")[:80],
            }
        )
    return out


def _build_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Агрегирует статистику за последние 7 дней (per-day bucket)."""
    now = time.time()
    horizon = now - 7 * 86400
    # day_key (YYYY-MM-DD UTC) → count
    per_day: dict[str, int] = {}
    by_emoji: dict[str, int] = {}
    total_7d = 0
    for evt in events:
        ts = float(evt.get("ts", 0.0) or 0.0)
        if ts < horizon:
            continue
        total_7d += 1
        # UTC date.
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
        per_day[date_str] = per_day.get(date_str, 0) + 1
        emoji = str(evt.get("emoji") or "")
        if emoji:
            by_emoji[emoji] = by_emoji.get(emoji, 0) + 1
    # Заполняем пропущенные дни нулями для ровного графика.
    today_utc = time.gmtime(now)
    filled: list[dict[str, Any]] = []
    for back in range(6, -1, -1):
        ts = now - back * 86400
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
        filled.append({"date": date_str, "count": per_day.get(date_str, 0)})
    # top emoji (top 10).
    top_emoji = sorted(by_emoji.items(), key=lambda kv: -kv[1])[:10]
    return {
        "total_7d": total_7d,
        "per_day": filled,
        "top_emoji": [{"emoji": e, "count": c} for e, c in top_emoji],
        "generated_ts": now,
        "_today_utc": time.strftime("%Y-%m-%d", today_utc),
    }


def _find_rule(rules: list[dict[str, Any]], rule_id: str) -> int:
    """Возвращает индекс правила по id или -1."""
    for idx, rule in enumerate(rules):
        if rule.get("id") == rule_id:
            return idx
    return -1


# ── factory ─────────────────────────────────────────────────────────────────


def build_reactions_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с endpoints управления auto-reaction rules."""
    router = APIRouter(tags=["reactions-admin"])

    # ── GET /api/admin/reactions/list ───────────────────────────────────────

    @router.get("/api/admin/reactions/list")
    async def reactions_list() -> dict:
        """JSON со списком правил, последних реакций и статистики."""
        try:
            rules = _load_rules()
            events = _tail_jsonl(_REACTIONS_LOG_FILE)
            recent = _build_recent(events)
            stats = _build_stats(events)
        except Exception as exc:  # noqa: BLE001
            _logger.error("reactions_admin.list_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"reactions_list_failed: {exc}",
            ) from exc

        enabled_count = sum(1 for r in rules if r.get("enabled"))
        return {
            "ok": True,
            "count": len(rules),
            "enabled_count": enabled_count,
            "rules": rules,
            "recent": recent,
            "stats": stats,
        }

    # ── POST /api/admin/reactions/add ───────────────────────────────────────

    @router.post("/api/admin/reactions/add")
    async def reactions_add(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создаёт новое правило. Body: ``{pattern, emoji, scope}``."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        pattern = _validate_pattern(str(payload.get("pattern") or ""))
        emoji = _validate_emoji(str(payload.get("emoji") or ""))
        scope = _validate_scope(str(payload.get("scope") or "any"))

        rules = _load_rules()
        # Защита от дублей по (pattern, emoji, scope).
        for rule in rules:
            if (
                rule.get("pattern") == pattern
                and rule.get("emoji") == emoji
                and rule.get("scope") == scope
            ):
                raise HTTPException(
                    status_code=409,
                    detail="reaction_rule_duplicate",
                )

        rule_id = uuid.uuid4().hex[:12]
        new_rule = {
            "id": rule_id,
            "pattern": pattern,
            "emoji": emoji,
            "scope": scope,
            "enabled": True,
            "created_ts": time.time(),
        }
        rules.append(new_rule)
        _save_rules(rules)
        _logger.info(
            "reactions_admin.add",
            id=rule_id,
            pattern_len=len(pattern),
            emoji=emoji,
            scope=scope,
        )
        return {"ok": True, "rule": new_rule}

    # ── POST /api/admin/reactions/toggle/{id} ───────────────────────────────

    @router.post("/api/admin/reactions/toggle/{rule_id}")
    async def reactions_toggle(
        rule_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Переключает enabled у правила. Возвращает обновлённое правило."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        rules = _load_rules()
        idx = _find_rule(rules, rule_id)
        if idx < 0:
            raise HTTPException(status_code=404, detail="reaction_rule_not_found")
        rules[idx]["enabled"] = not bool(rules[idx].get("enabled", True))
        _save_rules(rules)
        _logger.info(
            "reactions_admin.toggle",
            id=rule_id,
            enabled=rules[idx]["enabled"],
        )
        return {"ok": True, "rule": rules[idx]}

    # ── POST /api/admin/reactions/remove/{id} ───────────────────────────────

    @router.post("/api/admin/reactions/remove/{rule_id}")
    async def reactions_remove(
        rule_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Удаляет правило по id."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        rules = _load_rules()
        idx = _find_rule(rules, rule_id)
        if idx < 0:
            raise HTTPException(status_code=404, detail="reaction_rule_not_found")
        removed = rules.pop(idx)
        _save_rules(rules)
        _logger.info("reactions_admin.remove", id=rule_id)
        return {"ok": True, "removed": removed}

    # ── GET /admin/reactions — HTML ─────────────────────────────────────────

    @router.get("/admin/reactions", response_class=HTMLResponse)
    async def reactions_admin_page() -> HTMLResponse:
        """HTML страница с inline-формой + таблицами правил/событий/статистики."""
        return HTMLResponse(_REACTIONS_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/reactions ──────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — никаких
# innerHTML с внешними строками (XSS-safe для msg_preview, username, emoji).

_REACTIONS_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Reactions Admin</title>
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
        input[type="text"] { min-width: 220px; }
        select { min-width: 120px; }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.88rem;
        }
        th, td {
            padding: 7px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        th {
            background: #1a1a1a; color: var(--text-muted);
            text-transform: uppercase; font-size: 0.72rem;
            letter-spacing: 0.04em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125, 211, 252, 0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.72rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 4px 10px;
            font-size: 0.78rem;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
            margin-right: 4px;
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
        .hint { color: var(--text-muted); font-size: 0.74rem; margin-top: 6px; }
        .chart {
            display: flex; gap: 4px; align-items: flex-end;
            height: 80px; padding: 8px 0;
        }
        .chart-bar {
            flex: 1; background: rgba(125,211,252,0.3);
            border-radius: 2px 2px 0 0; min-height: 2px;
            position: relative;
        }
        .chart-bar:hover { background: rgba(125,211,252,0.6); }
        .chart-bar .bar-label {
            position: absolute; top: -16px; left: 50%;
            transform: translateX(-50%); font-size: 0.65rem;
            color: var(--text-muted);
        }
        .chart-bar .bar-date {
            position: absolute; bottom: -16px; left: 50%;
            transform: translateX(-50%); font-size: 0.62rem;
            color: var(--text-muted); white-space: nowrap;
        }
        .chart-container { padding-bottom: 22px; }
        .emoji-cell { font-size: 1.1rem; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Reactions Admin</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div class="card">
            <h2>Добавить правило реакции</h2>
            <div class="form-row">
                <input type="text" id="new-pattern" placeholder="regex pattern (напр. \\bспасибо\\b)" maxlength="256">
                <input type="text" id="new-emoji" placeholder="emoji (👍 или :fire:)" maxlength="32">
                <select id="new-scope">
                    <option value="any">scope: any</option>
                    <option value="chat">scope: chat</option>
                    <option value="user">scope: user</option>
                </select>
                <button id="btn-add">Создать</button>
            </div>
            <div class="hint">
                Pattern — Python regex (re.IGNORECASE). Emoji — Unicode emoji или :short_code:.
                Scope: any/chat/user.
            </div>
            <div id="form-err" class="err-banner" style="display:none; margin-top: 8px;"></div>
        </div>

        <div class="card">
            <h2>Реакции за последние 7 дней</h2>
            <div id="stats-summary" class="summary">—</div>
            <div class="chart-container">
                <div class="chart" id="chart-7d"></div>
            </div>
            <div id="top-emoji" class="summary" style="margin-top: 8px;">—</div>
        </div>

        <div id="summary" class="summary">Загружаем правила…</div>
        <div id="list-err" class="err-banner" style="display:none;"></div>
        <table id="rules-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Pattern</th>
                    <th>Emoji</th>
                    <th>Scope</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="rules-body"></tbody>
        </table>

        <h2 style="margin-top: 24px; font-size: 1rem; color: var(--accent);">Последние реакции</h2>
        <table id="recent-table">
            <thead>
                <tr>
                    <th>Когда</th>
                    <th>Chat</th>
                    <th>Msg</th>
                    <th>User</th>
                    <th>Emoji</th>
                    <th>Превью</th>
                </tr>
            </thead>
            <tbody id="recent-body"></tbody>
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
        function mkCell(content, cls) {
            const td = document.createElement('td');
            if (cls) td.className = cls;
            if (typeof content === 'string') td.textContent = content;
            else if (typeof content === 'number') td.textContent = String(content);
            else if (content instanceof Node) td.appendChild(content);
            else if (content === null || content === undefined) td.textContent = '—';
            return td;
        }
        function fmtTime(ts) {
            if (!ts) return '—';
            const d = new Date(ts * 1000);
            return d.toLocaleString('ru-RU', { hour12: false });
        }
        async function removeRule(id) {
            if (!confirm('Удалить правило ' + id + '?')) return;
            try {
                await callAdmin('POST', '/api/admin/reactions/remove/' + encodeURIComponent(id));
                fetchReactions();
            } catch (e) {
                alert('Не удалось удалить: ' + e.message);
            }
        }
        async function toggleRule(id) {
            try {
                await callAdmin('POST', '/api/admin/reactions/toggle/' + encodeURIComponent(id));
                fetchReactions();
            } catch (e) {
                alert('Не удалось переключить: ' + e.message);
            }
        }
        async function addRule() {
            const patternInput = document.getElementById('new-pattern');
            const emojiInput = document.getElementById('new-emoji');
            const scopeSel = document.getElementById('new-scope');
            const errBanner = document.getElementById('form-err');
            errBanner.style.display = 'none';
            errBanner.textContent = '';
            const pattern = (patternInput.value || '').trim();
            const emoji = (emojiInput.value || '').trim();
            const scope = scopeSel.value || 'any';
            if (!pattern || !emoji) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Pattern и emoji обязательны.';
                return;
            }
            try {
                await callAdmin('POST', '/api/admin/reactions/add',
                    { pattern: pattern, emoji: emoji, scope: scope });
                patternInput.value = '';
                emojiInput.value = '';
                scopeSel.value = 'any';
                fetchReactions();
            } catch (e) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Ошибка: ' + e.message;
            }
        }
        function renderStatusCell(rule) {
            const td = document.createElement('td');
            if (rule.enabled) {
                td.appendChild(mkBadge('enabled', 'badge-ok'));
            } else {
                td.appendChild(mkBadge('disabled', 'badge-muted'));
            }
            return td;
        }
        function renderActionsCell(rule) {
            const td = document.createElement('td');
            const toggleText = rule.enabled ? '⏸ off' : '▶ on';
            td.appendChild(mkButton(toggleText, () => toggleRule(rule.id)));
            td.appendChild(mkButton('🗑', () => removeRule(rule.id), 'danger'));
            return td;
        }
        function renderRulesTable(rules) {
            const tbody = document.getElementById('rules-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const rule of rules) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(rule.id, 'mono'));
                tr.appendChild(mkCell(rule.pattern, 'mono'));
                tr.appendChild(mkCell(rule.emoji, 'emoji-cell'));
                tr.appendChild(mkCell(rule.scope, 'mono'));
                tr.appendChild(renderStatusCell(rule));
                tr.appendChild(renderActionsCell(rule));
                tbody.appendChild(tr);
            }
        }
        function renderRecentTable(recent) {
            const tbody = document.getElementById('recent-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const evt of recent) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(fmtTime(evt.ts), 'mono'));
                tr.appendChild(mkCell(evt.chat_id, 'mono'));
                tr.appendChild(mkCell(evt.message_id, 'mono'));
                tr.appendChild(mkCell(evt.username || ('uid:' + (evt.user_id || '—'))));
                tr.appendChild(mkCell(evt.emoji, 'emoji-cell'));
                tr.appendChild(mkCell(evt.msg_preview || ''));
                tbody.appendChild(tr);
            }
        }
        function renderChart(stats) {
            const chart = document.getElementById('chart-7d');
            while (chart.firstChild) chart.removeChild(chart.firstChild);
            const perDay = stats.per_day || [];
            if (!perDay.length) return;
            const maxCount = Math.max(1, ...perDay.map(d => d.count || 0));
            for (const day of perDay) {
                const bar = document.createElement('div');
                bar.className = 'chart-bar';
                const pct = ((day.count || 0) / maxCount) * 100;
                bar.style.height = Math.max(2, pct) + '%';
                const lab = document.createElement('span');
                lab.className = 'bar-label';
                lab.textContent = String(day.count || 0);
                bar.appendChild(lab);
                const dat = document.createElement('span');
                dat.className = 'bar-date';
                dat.textContent = (day.date || '').slice(5);
                bar.appendChild(dat);
                chart.appendChild(bar);
            }
            const summary = document.getElementById('stats-summary');
            summary.textContent = 'Всего за 7 дней: ' + (stats.total_7d || 0);
            const topEl = document.getElementById('top-emoji');
            while (topEl.firstChild) topEl.removeChild(topEl.firstChild);
            const topLabel = document.createElement('span');
            topLabel.textContent = 'Top emoji: ';
            topEl.appendChild(topLabel);
            for (const entry of (stats.top_emoji || [])) {
                const span = document.createElement('span');
                span.style.marginRight = '10px';
                span.textContent = entry.emoji + ' ' + entry.count;
                topEl.appendChild(span);
            }
        }
        async function fetchReactions() {
            const errBanner = document.getElementById('list-err');
            errBanner.style.display = 'none';
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/reactions/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const rules = data.rules || [];
                const recent = data.recent || [];
                const stats = data.stats || {};
                renderRulesTable(rules);
                renderRecentTable(recent);
                renderChart(stats);
                const summary = document.getElementById('summary');
                summary.textContent = 'Всего правил: ' + rules.length +
                    ' · активных: ' + (data.enabled_count || 0) +
                    ' · недавних реакций: ' + recent.length;
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                errBanner.style.display = 'block';
                errBanner.textContent = 'Ошибка загрузки: ' + e.message;
            }
        }
        document.getElementById('btn-add').addEventListener('click', addRule);
        fetchReactions();
        setInterval(fetchReactions, 30000);
    </script>
</body>
</html>
"""
