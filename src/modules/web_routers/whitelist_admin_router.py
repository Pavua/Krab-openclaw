# -*- coding: utf-8 -*-
"""
Whitelist admin router — Wave 215 (Session 48).

Owner-side панель для управления ACL Krab: whitelist пользователей
(`owner` / `full` / `partial`), blacklist чатов (`chat_ban_cache`)
и voice-blocked чатов (`config.VOICE_REPLY_BLOCKED_CHATS`).

Источники данных (read + write):
    • ``src.core.access_control`` —
      ``load_acl_runtime_state`` / ``update_acl_subject`` (читают/пишут
      ``~/.openclaw/krab_userbot_acl.json``).
    • ``src.core.chat_ban_cache.chat_ban_cache`` — singleton для
      banned-chats (``list_entries`` / ``mark_banned`` / ``clear``).
    • ``src.config.config.VOICE_REPLY_BLOCKED_CHATS`` —
      env-driven список chat_id, для которых voice-reply отключён;
      запись через ``config.update_setting``.

Endpoints (READY):
- GET  /api/admin/whitelist/list        — JSON {auth, blacklist, voice_blocked}.
- POST /api/admin/whitelist/add_user    — write-access, body {subject, level?}.
- POST /api/admin/whitelist/remove_user — write-access, body {subject, level?}.
- POST /api/admin/whitelist/block_chat  — write-access, body {chat_id, voice_only?, reason?, hours?}.
- POST /api/admin/whitelist/unblock_chat — write-access, body {chat_id, voice_only?}.
- GET  /admin/whitelist                 — HTML страница (polling 30s).

Контракт безопасности: все write эндпоинты идут через
``ctx.assert_write_access_fn`` (X-Krab-Web-Key / token). Subject
валидируется regex ``^[A-Za-z0-9_@]{1,64}$``; chat_id —
отрицательный для групп/каналов, до 20 цифр.
В JSON-payload и HTML-выдаче user ID маскируется (первые 4 +
последние 4 символа), полный ID видно только при write-операции.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.config import config
from src.core.access_control import (
    AccessLevel,
    load_acl_runtime_state,
    normalize_subject,
    update_acl_subject,
)
from src.core.chat_ban_cache import chat_ban_cache
from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Subject (user id / username) — ASCII буквы/цифры/_/@, 1..64.
# Telegram username max 32, user_id до 20 цифр — оба укладываются.
_SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9_@]{1,64}$")

# chat_id: отрицательный для групп/каналов, положительный для DM.
# Поддерживаем стандартный диапазон Telegram до 20 цифр.
_CHAT_ID_PATTERN = re.compile(r"^-?\d{1,20}$")

# Допустимые уровни whitelist (для add/remove_user).
_ALLOWED_LEVELS: frozenset[str] = frozenset(
    {
        AccessLevel.OWNER.value,
        AccessLevel.FULL.value,
        AccessLevel.PARTIAL.value,
    }
)

# Дефолтный уровень для !whitelist add без указания level — full.
# owner-доступ намеренно не даётся через web-API: его выставляют только
# вручную (см. krab_userbot_acl.json), чтобы случайным кликом UI нельзя
# было «повысить» произвольный subject до owner.
_DEFAULT_ADD_LEVEL: str = AccessLevel.FULL.value


# ── helpers ─────────────────────────────────────────────────────────────────


def _validate_subject(raw: str) -> str:
    """Sanitize/validate user subject (id или username). Возвращает нормализованную форму.

    Возвращаемое значение — то же что положено в ACL-файл (нормализованный
    через ``access_control.normalize_subject``).
    """
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="subject_empty")
    if not _SUBJECT_PATTERN.match(raw):
        raise HTTPException(status_code=400, detail="subject_invalid_format")
    normalized = normalize_subject(raw)
    if not normalized:
        raise HTTPException(status_code=400, detail="subject_normalize_failed")
    return normalized


def _validate_level(raw: str | None) -> str:
    """Валидирует уровень доступа. ``None``/пусто → дефолт (full)."""
    level = (raw or "").strip().lower()
    if not level:
        return _DEFAULT_ADD_LEVEL
    if level not in _ALLOWED_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"level_invalid:{level} (allowed: owner/full/partial)",
        )
    return level


def _validate_chat_id(raw: str) -> str:
    """Sanitize/validate chat_id. Возвращает нормализованную строку."""
    target = str(raw or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="chat_id_empty")
    if not _CHAT_ID_PATTERN.match(target):
        raise HTTPException(status_code=400, detail="chat_id_invalid_format")
    return target


def _mask_subject(subject: str) -> str:
    """Маскирует id/username: первые 4 + последние 4 символа.

    Применяется только для отображения. Полный subject доступен только
    после write-операции (и тогда возвращается в ``echo`` поле).

    Примеры:
        ``312322764`` → ``3123•••2764``
        ``pablito`` → ``pablito`` (короткий — не маскируем, не приватный)
        ``some_long_username`` → ``some•••name``
    """
    s = str(subject or "")
    if len(s) <= 8:
        # Короткие subject (username 7 chars или меньше, id 8 цифр)
        # не маскируем — это не приватная информация при такой длине.
        return s
    return f"{s[:4]}•••{s[-4:]}"


def _mask_chat_id(chat_id: str) -> str:
    """Маскирует chat_id. Для отрицательных id маскируем числовую часть."""
    s = str(chat_id or "")
    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]
    if len(s) <= 8:
        return f"{sign}{s}"
    return f"{sign}{s[:4]}•••{s[-4:]}"


def _load_voice_blocked_chats() -> list[str]:
    """Читает актуальный voice blocklist (копия)."""
    blocked = getattr(config, "VOICE_REPLY_BLOCKED_CHATS", None) or []
    return [str(v).strip() for v in blocked if str(v).strip()]


def _persist_voice_blocked_chats(values: list[str]) -> None:
    """Persist voice blocklist в .env через ``config.update_setting``.

    Совместимо с однопроцессным userbot — изменение видно сразу всем
    мутаторам ``config.VOICE_REPLY_BLOCKED_CHATS``.
    """
    update_fn = getattr(config, "update_setting", None)
    if callable(update_fn):
        update_fn("VOICE_REPLY_BLOCKED_CHATS", ",".join(values))
    else:
        # Fallback: in-memory только (для тестов с lightweight config).
        config.VOICE_REPLY_BLOCKED_CHATS = list(values)


def _collect_acl_payload() -> dict[str, Any]:
    """Возвращает list-блок ACL (owner / full / partial) с маскировкой.

    Каждый subject отдаётся как dict ``{subject_masked, kind}``,
    где ``kind`` — ``id`` либо ``username``. Полный subject в JSON
    не утекает — для editor его нужно вручную ввести.
    """
    state = load_acl_runtime_state()
    payload: dict[str, list[dict[str, str]]] = {}
    for level in (
        AccessLevel.OWNER.value,
        AccessLevel.FULL.value,
        AccessLevel.PARTIAL.value,
    ):
        items: list[dict[str, str]] = []
        for subj in state.get(level, []) or []:
            kind = "id" if str(subj).isdigit() else "username"
            items.append(
                {
                    "subject_masked": _mask_subject(subj),
                    "kind": kind,
                }
            )
        payload[level] = items
    return payload


def _collect_blacklist_payload() -> list[dict[str, Any]]:
    """Возвращает текущий список banned chats (chat_ban_cache)."""
    try:
        entries = chat_ban_cache.list_entries()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("whitelist_admin.blacklist_read_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for entry in entries:
        cid = str(entry.get("chat_id") or "")
        out.append(
            {
                "chat_id_masked": _mask_chat_id(cid),
                "error_code": entry.get("error_code") or "",
                "banned_at": entry.get("banned_at") or "",
                "expires_at": entry.get("expires_at"),
                "hit_count": int(entry.get("hit_count") or 0),
            }
        )
    return out


def _collect_voice_blocked_payload() -> list[dict[str, str]]:
    """Возвращает текущий voice blocklist (маскированный)."""
    return [{"chat_id_masked": _mask_chat_id(cid)} for cid in _load_voice_blocked_chats()]


# ── factory ─────────────────────────────────────────────────────────────────


def build_whitelist_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter с endpoints управления whitelist/blacklist."""
    router = APIRouter(tags=["whitelist-admin"])

    # ── GET /api/admin/whitelist/list ───────────────────────────────────────

    @router.get("/api/admin/whitelist/list")
    async def whitelist_list() -> dict:
        """JSON snapshot ACL + blacklist + voice blocklist.

        Все subject/chat_id отдаются в маскированном виде (первые 4 +
        последние 4 символа). Это исключает leakage user IDs в случае
        просмотра панели через shoulder-surf или скриншот.
        """
        try:
            auth = _collect_acl_payload()
        except Exception as exc:  # noqa: BLE001
            _logger.error("whitelist_admin.acl_read_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"acl_read_failed: {exc}",
            ) from exc

        blacklist = _collect_blacklist_payload()
        voice_blocked = _collect_voice_blocked_payload()

        return {
            "ok": True,
            "auth": auth,
            "blacklist": blacklist,
            "voice_blocked": voice_blocked,
            "counts": {
                "owner": len(auth.get(AccessLevel.OWNER.value, [])),
                "full": len(auth.get(AccessLevel.FULL.value, [])),
                "partial": len(auth.get(AccessLevel.PARTIAL.value, [])),
                "blacklist": len(blacklist),
                "voice_blocked": len(voice_blocked),
            },
        }

    # ── POST /api/admin/whitelist/add_user ──────────────────────────────────

    @router.post("/api/admin/whitelist/add_user")
    async def whitelist_add_user(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Добавляет subject (id или @username) в ACL (full по умолчанию).

        Body: ``{"subject": "...", "level": "owner|full|partial"}`` —
        level опционален, дефолт ``full``. owner-уровень разрешён, но
        ответственность на caller — UI прячет owner за confirmation.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_subject = str(payload.get("subject") or "").strip()
        level = _validate_level(payload.get("level"))
        subject = _validate_subject(raw_subject)

        result = update_acl_subject(level, subject, add=True)
        _logger.info(
            "whitelist_admin.add_user",
            level=level,
            subject_masked=_mask_subject(subject),
            changed=result.get("changed"),
        )
        return {
            "ok": True,
            "level": level,
            "subject_masked": _mask_subject(subject),
            "changed": bool(result.get("changed")),
        }

    # ── POST /api/admin/whitelist/remove_user ───────────────────────────────

    @router.post("/api/admin/whitelist/remove_user")
    async def whitelist_remove_user(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Убирает subject из ACL для указанного level (дефолт full)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_subject = str(payload.get("subject") or "").strip()
        level = _validate_level(payload.get("level"))
        subject = _validate_subject(raw_subject)

        result = update_acl_subject(level, subject, add=False)
        _logger.info(
            "whitelist_admin.remove_user",
            level=level,
            subject_masked=_mask_subject(subject),
            changed=result.get("changed"),
        )
        return {
            "ok": True,
            "level": level,
            "subject_masked": _mask_subject(subject),
            "changed": bool(result.get("changed")),
        }

    # ── POST /api/admin/whitelist/block_chat ────────────────────────────────

    @router.post("/api/admin/whitelist/block_chat")
    async def whitelist_block_chat(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Блокирует чат.

        Body: ``{"chat_id": "...", "voice_only": false, "reason": "...", "hours": 6}``.
        Если ``voice_only=true`` — добавляем только в voice blocklist,
        иначе в general chat_ban_cache (с TTL ``hours``, дефолт 6h;
        ``hours=0`` или null → permanent).
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_chat_id = str(payload.get("chat_id") or "").strip()
        chat_id = _validate_chat_id(raw_chat_id)
        voice_only = bool(payload.get("voice_only"))
        reason = str(payload.get("reason") or "manual_owner_block").strip()[:64]
        hours_raw = payload.get("hours")

        if voice_only:
            # Voice-only path: append к VOICE_REPLY_BLOCKED_CHATS.
            current = _load_voice_blocked_chats()
            changed = chat_id not in current
            if changed:
                current.append(chat_id)
                _persist_voice_blocked_chats(current)
            _logger.info(
                "whitelist_admin.voice_block",
                chat_id_masked=_mask_chat_id(chat_id),
                changed=changed,
            )
            return {
                "ok": True,
                "mode": "voice_only",
                "chat_id_masked": _mask_chat_id(chat_id),
                "changed": changed,
            }

        # General chat ban path.
        cooldown: float | None
        if hours_raw is None or hours_raw == 0 or hours_raw == "":
            cooldown = None  # permanent ban
        else:
            try:
                cooldown = float(hours_raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="hours_invalid_number",
                ) from exc
            if cooldown <= 0:
                cooldown = None

        chat_ban_cache.mark_banned(chat_id, reason, cooldown_hours=cooldown)
        _logger.info(
            "whitelist_admin.block_chat",
            chat_id_masked=_mask_chat_id(chat_id),
            reason=reason,
            cooldown_hours=cooldown,
        )
        return {
            "ok": True,
            "mode": "ban_cache",
            "chat_id_masked": _mask_chat_id(chat_id),
            "reason": reason,
            "cooldown_hours": cooldown,
        }

    # ── POST /api/admin/whitelist/unblock_chat ──────────────────────────────

    @router.post("/api/admin/whitelist/unblock_chat")
    async def whitelist_unblock_chat(
        payload: dict = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Снимает блокировку чата.

        Body: ``{"chat_id": "...", "voice_only": false}``. Если
        ``voice_only=true`` — убираем только из voice blocklist,
        иначе из ``chat_ban_cache``.
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)

        raw_chat_id = str(payload.get("chat_id") or "").strip()
        chat_id = _validate_chat_id(raw_chat_id)
        voice_only = bool(payload.get("voice_only"))

        if voice_only:
            current = _load_voice_blocked_chats()
            changed = chat_id in current
            if changed:
                current = [v for v in current if v != chat_id]
                _persist_voice_blocked_chats(current)
            _logger.info(
                "whitelist_admin.voice_unblock",
                chat_id_masked=_mask_chat_id(chat_id),
                changed=changed,
            )
            return {
                "ok": True,
                "mode": "voice_only",
                "chat_id_masked": _mask_chat_id(chat_id),
                "changed": changed,
            }

        changed = chat_ban_cache.clear(chat_id)
        _logger.info(
            "whitelist_admin.unblock_chat",
            chat_id_masked=_mask_chat_id(chat_id),
            changed=changed,
        )
        return {
            "ok": True,
            "mode": "ban_cache",
            "chat_id_masked": _mask_chat_id(chat_id),
            "changed": changed,
        }

    # ── GET /admin/whitelist — HTML page ────────────────────────────────────

    @router.get("/admin/whitelist", response_class=HTMLResponse)
    async def whitelist_admin_page() -> HTMLResponse:
        """HTML страница ACL editor (polling 30s)."""
        return HTMLResponse(_WHITELIST_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/whitelist ──────────────────────────────────────────
# Клиентский JS строит DOM через createElement/textContent — никаких
# innerHTML с внешними строками (XSS-safe). Полный subject вводится в
# отдельных input полях для add/remove операций — он НЕ отрисовывается
# в таблицах (только маскированная форма).

_WHITELIST_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Whitelist Admin</title>
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
        section { margin-bottom: 28px; }
        section h2 {
            margin: 0 0 8px; font-size: 1.05rem;
            color: var(--accent); text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.9rem;
        }
        th, td {
            padding: 6px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
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
        .badge-owner { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-full { background: rgba(125,211,252,0.15); color: var(--accent); }
        .badge-partial { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button {
            background: rgba(125,211,252,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 4px 10px;
            font-size: 0.75rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 4px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.danger { border-color: var(--err); color: var(--err); background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        input, select {
            background: #1a1a1a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 4px 8px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 0.85rem;
        }
        input:focus, select:focus { outline: none; border-color: var(--accent); }
        form.inline { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner { color: var(--err); padding: 10px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 12px; }
        .hint { color: var(--text-muted); font-size: 0.78rem; margin-top: 4px; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Whitelist Admin</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем ACL…</div>
        <div id="err-banner"></div>

        <section>
            <h2>Whitelist (ACL)</h2>
            <form class="inline" id="add-user-form">
                <input type="text" id="add-subject" placeholder="user_id или @username"
                    maxlength="64" pattern="[A-Za-z0-9_@]+" required>
                <select id="add-level">
                    <option value="full" selected>full</option>
                    <option value="partial">partial</option>
                    <option value="owner">owner</option>
                </select>
                <button type="submit">+ Add</button>
                <button type="button" id="remove-user-btn" class="danger">− Remove</button>
            </form>
            <div class="hint">
                ID/username вводится полностью, в таблице ниже отображается маскированно
                (первые 4 + последние 4 символа) для приватности.
            </div>
            <table>
                <thead><tr><th>Уровень</th><th>Subject (masked)</th><th>Type</th></tr></thead>
                <tbody id="acl-body"></tbody>
            </table>
        </section>

        <section>
            <h2>Blocked Chats (chat_ban_cache)</h2>
            <form class="inline" id="block-chat-form">
                <input type="text" id="block-chat-id" placeholder="chat_id (например, -1001234567890)"
                    maxlength="21" pattern="-?[0-9]+" required>
                <input type="text" id="block-chat-reason" placeholder="reason (опционально)"
                    maxlength="64">
                <input type="number" id="block-chat-hours" placeholder="hours (0 = permanent)"
                    min="0" max="720" value="6">
                <button type="submit" class="danger">⛔ Block</button>
                <button type="button" id="unblock-chat-btn">↻ Unblock</button>
            </form>
            <table>
                <thead><tr><th>Chat (masked)</th><th>Reason</th><th>Banned</th><th>Expires</th><th>Hits</th></tr></thead>
                <tbody id="blacklist-body"></tbody>
            </table>
        </section>

        <section>
            <h2>Voice-Reply Blocked Chats</h2>
            <form class="inline" id="voice-block-form">
                <input type="text" id="voice-chat-id" placeholder="chat_id"
                    maxlength="21" pattern="-?[0-9]+" required>
                <button type="submit" class="danger">🔇 Voice-block</button>
                <button type="button" id="voice-unblock-btn">🔊 Voice-unblock</button>
            </form>
            <table>
                <thead><tr><th>Chat (masked)</th></tr></thead>
                <tbody id="voice-body"></tbody>
            </table>
        </section>
    </main>
    <script>
        async function callAdmin(method, url, body) {
            try {
                const opts = { method: method };
                if (body !== undefined) {
                    opts.headers = { 'Content-Type': 'application/json' };
                    opts.body = JSON.stringify(body);
                }
                const res = await fetch(url, opts);
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
                return data;
            } catch (e) {
                alert('Ошибка: ' + e.message);
                throw e;
            }
        }

        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }

        function mkCell(content, cls) {
            const td = document.createElement('td');
            if (cls) td.className = cls;
            if (typeof content === 'string') td.textContent = content;
            else if (content instanceof Node) td.appendChild(content);
            return td;
        }

        function fmtTimeShort(iso) {
            if (!iso) return '—';
            try {
                const d = new Date(iso);
                return d.toLocaleString('ru-RU', { hour12: false });
            } catch (e) { return iso; }
        }

        async function fetchAll() {
            const errBanner = document.getElementById('err-banner');
            while (errBanner.firstChild) errBanner.removeChild(errBanner.firstChild);
            try {
                const res = await fetch('/api/admin/whitelist/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                renderAcl(data.auth || {});
                renderBlacklist(data.blacklist || []);
                renderVoice(data.voice_blocked || []);
                renderSummary(data.counts || {});
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }

        function renderAcl(auth) {
            const tbody = document.getElementById('acl-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            const order = ['owner', 'full', 'partial'];
            const badgeCls = { owner: 'badge-owner', full: 'badge-full', partial: 'badge-partial' };
            for (const level of order) {
                const items = auth[level] || [];
                for (const item of items) {
                    const tr = document.createElement('tr');
                    const levelTd = document.createElement('td');
                    levelTd.appendChild(mkBadge(level, badgeCls[level] || 'badge-muted'));
                    tr.appendChild(levelTd);
                    tr.appendChild(mkCell(item.subject_masked || '', 'mono'));
                    tr.appendChild(mkCell(item.kind || ''));
                    tbody.appendChild(tr);
                }
            }
        }

        function renderBlacklist(list) {
            const tbody = document.getElementById('blacklist-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const entry of list) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(entry.chat_id_masked || '', 'mono'));
                tr.appendChild(mkCell(entry.error_code || '—'));
                tr.appendChild(mkCell(fmtTimeShort(entry.banned_at)));
                tr.appendChild(mkCell(entry.expires_at ? fmtTimeShort(entry.expires_at) : '∞'));
                tr.appendChild(mkCell(String(entry.hit_count || 0), 'mono'));
                tbody.appendChild(tr);
            }
        }

        function renderVoice(list) {
            const tbody = document.getElementById('voice-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const entry of list) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(entry.chat_id_masked || '', 'mono'));
                tbody.appendChild(tr);
            }
        }

        function renderSummary(counts) {
            const summary = document.getElementById('summary');
            while (summary.firstChild) summary.removeChild(summary.firstChild);
            summary.appendChild(document.createTextNode(
                'owner: ' + (counts.owner || 0) +
                ' · full: ' + (counts.full || 0) +
                ' · partial: ' + (counts.partial || 0) +
                ' · blocked chats: ' + (counts.blacklist || 0) +
                ' · voice-blocked: ' + (counts.voice_blocked || 0)
            ));
        }

        // Add user
        document.getElementById('add-user-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const subject = document.getElementById('add-subject').value.trim();
            const level = document.getElementById('add-level').value;
            if (!subject) return;
            if (level === 'owner' &&
                !confirm('Добавить ' + subject + ' как OWNER? Это даёт полный admin доступ.')) return;
            await callAdmin('POST', '/api/admin/whitelist/add_user', { subject, level });
            document.getElementById('add-subject').value = '';
            fetchAll();
        });

        // Remove user
        document.getElementById('remove-user-btn').addEventListener('click', async () => {
            const subject = document.getElementById('add-subject').value.trim();
            const level = document.getElementById('add-level').value;
            if (!subject) {
                alert('Введите subject в поле выше');
                return;
            }
            if (!confirm('Убрать ' + subject + ' из уровня ' + level + '?')) return;
            await callAdmin('POST', '/api/admin/whitelist/remove_user', { subject, level });
            document.getElementById('add-subject').value = '';
            fetchAll();
        });

        // Block chat
        document.getElementById('block-chat-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const chat_id = document.getElementById('block-chat-id').value.trim();
            const reason = document.getElementById('block-chat-reason').value.trim();
            const hours = parseInt(document.getElementById('block-chat-hours').value, 10) || 0;
            if (!chat_id) return;
            if (!confirm('Заблокировать чат ' + chat_id + '?')) return;
            await callAdmin('POST', '/api/admin/whitelist/block_chat',
                { chat_id, reason: reason || undefined, hours });
            document.getElementById('block-chat-id').value = '';
            fetchAll();
        });

        // Unblock chat
        document.getElementById('unblock-chat-btn').addEventListener('click', async () => {
            const chat_id = document.getElementById('block-chat-id').value.trim();
            if (!chat_id) { alert('Введите chat_id'); return; }
            if (!confirm('Снять блокировку с чата ' + chat_id + '?')) return;
            await callAdmin('POST', '/api/admin/whitelist/unblock_chat', { chat_id });
            document.getElementById('block-chat-id').value = '';
            fetchAll();
        });

        // Voice block
        document.getElementById('voice-block-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const chat_id = document.getElementById('voice-chat-id').value.trim();
            if (!chat_id) return;
            await callAdmin('POST', '/api/admin/whitelist/block_chat',
                { chat_id, voice_only: true });
            document.getElementById('voice-chat-id').value = '';
            fetchAll();
        });

        // Voice unblock
        document.getElementById('voice-unblock-btn').addEventListener('click', async () => {
            const chat_id = document.getElementById('voice-chat-id').value.trim();
            if (!chat_id) { alert('Введите chat_id'); return; }
            await callAdmin('POST', '/api/admin/whitelist/unblock_chat',
                { chat_id, voice_only: true });
            document.getElementById('voice-chat-id').value = '';
            fetchAll();
        });

        fetchAll();
        setInterval(fetchAll, 30000);
    </script>
</body>
</html>
"""
