# -*- coding: utf-8 -*-
"""
Silence admin router — Wave 199 (Session 48).

Owner-side панель для управления режимом тишины Krab. Объединяет два
источника данных:
  * ``silence_manager`` (``src.core.silence_mode``) — in-memory per-chat
    и глобальный mute с expiry на ``time.monotonic()``.
  * ``silence_schedule_manager`` (``src.core.silence_schedule``) —
    persistent JSON-state (``~/.openclaw/krab_runtime_state/silence_schedule.json``)
    с расписанием вида HH:MM-HH:MM, который автоматически включает
    глобальный mute в заданное окно.

Endpoints (READY):
- GET  /api/admin/silence/list   — {active, scheduled, stats}.
- POST /api/admin/silence/add    — write-access; {chat_id, duration_minutes?, reason?}.
- POST /api/admin/silence/remove — write-access; {chat_id}.
- GET  /admin/silence            — HTML page (polling 15s).

Контракт безопасности: write-endpoints идут через
``ctx.assert_write_access_fn(x_krab_web_key, token)``; ``chat_id``
валидируется regex (только digits + optional leading ``-``).
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger
from src.core.silence_mode import silence_manager
from src.core.silence_schedule import silence_schedule_manager

from ._context import RouterContext

_logger = get_logger(__name__)

# Telegram chat_id — int (положительный для users, отрицательный для групп).
# Принимаем только digits + optional leading "-". Длина — до 20 символов
# чтобы покрыть и user_id, и -100xxxxxxxxxx групповые id.
_CHAT_ID_PATTERN = re.compile(r"^-?\d{1,20}$")

# Дефолтные значения — должны совпадать с константами в silence_mode.py
_DEFAULT_DURATION_MIN = 30
_MAX_DURATION_MIN = 60 * 24 * 7  # неделя — sanity-cap

# Reason — короткая метаданная (хранится только в логах, не в state).
_MAX_REASON_LEN = 200

# Дополнительный in-process side-state: chat_id → {since_ts, reason}.
# Не персистится (silence_manager сам по себе in-memory) — переживёт
# только до restart. Использует wallclock time.time() чтобы UI мог
# показать since_ts. Очищается лениво при list().
_silence_meta: dict[str, dict[str, Any]] = {}


def _validate_chat_id(chat_id: str | int | None) -> str:
    """Sanitize/validate chat_id перед использованием в silence_manager."""
    if chat_id is None:
        raise HTTPException(status_code=400, detail="silence_chat_id_missing")
    chat_id_str = str(chat_id).strip()
    if not chat_id_str or not _CHAT_ID_PATTERN.match(chat_id_str):
        raise HTTPException(status_code=400, detail="silence_chat_id_invalid")
    return chat_id_str


def _validate_duration(duration: Any) -> int:
    """Валидация длительности тишины в минутах. None → default."""
    if duration is None or duration == "":
        return _DEFAULT_DURATION_MIN
    try:
        value = int(duration)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="silence_duration_invalid") from exc
    if value <= 0:
        raise HTTPException(status_code=400, detail="silence_duration_must_be_positive")
    if value > _MAX_DURATION_MIN:
        raise HTTPException(
            status_code=400,
            detail=f"silence_duration_too_long_max_{_MAX_DURATION_MIN}",
        )
    return value


def _sanitize_reason(reason: Any) -> str:
    """Reason — строка, обрезается до _MAX_REASON_LEN, теги/опасные символы
    не пропускаем в state (только в логи)."""
    if reason is None:
        return ""
    text = str(reason).strip()
    if not text:
        return ""
    # Срезаем угловые скобки и backtick — паранойя для логов/UI.
    text = re.sub(r"[<>`]", "", text)
    return text[:_MAX_REASON_LEN]


def _purge_expired_meta() -> None:
    """Удаляет meta-записи для чатов, которые уже не в _chat_mutes."""
    active_chats = set(silence_manager._chat_mutes.keys())  # noqa: SLF001
    expired = [cid for cid in _silence_meta if cid not in active_chats]
    for cid in expired:
        _silence_meta.pop(cid, None)


def _build_active_list() -> list[dict[str, Any]]:
    """Возвращает список активных silence-записей.

    Источник правды — silence_manager._chat_mutes (monotonic expiry).
    Дополняем wallclock since_ts/reason из _silence_meta где доступно.
    """
    _purge_expired_meta()
    now_mono = time.monotonic()
    now_wall = time.time()
    result: list[dict[str, Any]] = []
    # Снапшот dict items — silence_manager модифицирует _chat_mutes при
    # is_chat_muted() expired-cleanup, не итерируем напрямую.
    items = list(silence_manager._chat_mutes.items())  # noqa: SLF001
    for chat_id, expiry_mono in items:
        remaining = expiry_mono - now_mono
        if remaining <= 0:
            continue
        meta = _silence_meta.get(chat_id, {})
        since_ts = meta.get("since_ts")
        # Если мета нет (например, mute поставлен через !cmd) — оцениваем
        # since как (now - что было до expiry). Без duration не знаем точно,
        # поэтому показываем `since_ts=None` (UI покажет "—").
        result.append(
            {
                "chat_id": chat_id,
                "label": meta.get("label", chat_id),
                "since_ts": since_ts,
                "since_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_ts))
                    if isinstance(since_ts, (int, float))
                    else None
                ),
                "expiry_wall_ts": now_wall + remaining,
                "expiry_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(now_wall + remaining),
                ),
                "remaining_sec": round(remaining, 1),
                "remaining_min": round(remaining / 60, 2),
                "reason": meta.get("reason", ""),
            }
        )
    result.sort(key=lambda x: x["remaining_sec"])
    return result


def _build_scheduled_block() -> dict[str, Any]:
    """Снимок silence_schedule_manager.get_status() для JSON-ответа."""
    status = silence_schedule_manager.get_status()
    return {
        "enabled": bool(status.get("enabled")),
        "start": status.get("start"),
        "end": status.get("end"),
        "active_now": bool(status.get("active_now")),
    }


def _build_stats(active: list[dict[str, Any]], scheduled: dict[str, Any]) -> dict[str, Any]:
    """Считает summary: silenced_now / global / next scheduled."""
    silenced_now = len(active)
    global_muted = silence_manager.is_global_muted()
    if global_muted:
        silenced_now += 1
    return {
        "silenced_now": silenced_now,
        "active_per_chat": len(active),
        "global_muted": global_muted,
        "global_remaining_min": round(
            silence_manager.global_mute_remaining_sec() / 60, 2,
        ),
        "scheduled_enabled": scheduled["enabled"],
        "scheduled_active_now": scheduled["active_now"],
        "scheduled_window": (
            f"{scheduled['start']}–{scheduled['end']}"
            if scheduled["enabled"] and scheduled["start"] and scheduled["end"]
            else None
        ),
    }


def build_silence_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter для управления silence."""
    router = APIRouter(tags=["silence-admin"])

    # ── GET /api/admin/silence/list ─────────────────────────────────────────

    @router.get("/api/admin/silence/list")
    async def silence_list() -> dict[str, Any]:
        """Возвращает {active, scheduled, stats}.

        - ``active``: список чатов с активным mute (chat_id, label,
          since_ts, remaining_sec, reason).
        - ``scheduled``: расписание из silence_schedule_manager (start/end,
          enabled, active_now).
        - ``stats``: silenced_now (с учётом global), global_muted,
          scheduled_window.
        """
        try:
            active = _build_active_list()
            scheduled = _build_scheduled_block()
            stats = _build_stats(active, scheduled)
        except Exception as exc:  # noqa: BLE001
            _logger.error("silence_admin.list_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"silence_list_failed: {exc}",
            ) from exc
        return {
            "ok": True,
            "active": active,
            "scheduled": scheduled,
            "stats": stats,
        }

    # ── POST /api/admin/silence/add ─────────────────────────────────────────

    @router.post("/api/admin/silence/add")
    async def silence_add(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Добавляет per-chat silence на указанную длительность.

        Body:
          - chat_id: str|int (обязателен, Telegram chat id).
          - duration_minutes: int (опционально, default 30, max 7 days).
          - reason: str (опционально, для логов).
        """
        ctx.assert_write_access_fn(x_krab_web_key, token)
        chat_id = _validate_chat_id(payload.get("chat_id"))
        duration_min = _validate_duration(payload.get("duration_minutes"))
        reason = _sanitize_reason(payload.get("reason"))

        expiry_mono = silence_manager.mute_chat(chat_id, minutes=duration_min)
        # Сохраняем wallclock meta для UI (since_ts/reason). monotonic
        # expiry уже в silence_manager.
        _silence_meta[chat_id] = {
            "since_ts": time.time(),
            "reason": reason,
            "label": chat_id,
            "duration_min": duration_min,
        }
        _logger.info(
            "silence_admin.add",
            chat_id=chat_id,
            duration_minutes=duration_min,
            reason=reason or "",
        )
        return {
            "ok": True,
            "chat_id": chat_id,
            "duration_minutes": duration_min,
            "reason": reason,
            "expiry_mono": expiry_mono,
            "expiry_wall_ts": time.time() + duration_min * 60,
        }

    # ── POST /api/admin/silence/remove ──────────────────────────────────────

    @router.post("/api/admin/silence/remove")
    async def silence_remove(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Снимает per-chat silence."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        chat_id = _validate_chat_id(payload.get("chat_id"))
        was_muted = silence_manager.unmute_chat(chat_id)
        _silence_meta.pop(chat_id, None)
        _logger.info(
            "silence_admin.remove",
            chat_id=chat_id,
            was_muted=was_muted,
        )
        return {
            "ok": True,
            "chat_id": chat_id,
            "was_muted": was_muted,
        }

    # ── GET /admin/silence — HTML ───────────────────────────────────────────

    @router.get("/admin/silence", response_class=HTMLResponse)
    async def silence_admin_page() -> HTMLResponse:
        """HTML страница со списком активных silence + schedule (polling 15s)."""
        return HTMLResponse(_SILENCE_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/silence ─────────────────────────────────────────────
# DOM-операции через createElement/textContent (XSS-safe): любые
# user-controlled поля (chat_id, label, reason) идут через textContent.

_SILENCE_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Silence Admin</title>
    <style>
        :root {
            --bg: #0d0d0d;
            --card-bg: #121212;
            --border: #2a2a2a;
            --text: #e0e0e0;
            --text-muted: #888888;
            --accent: #a78bfa;
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
        section { margin-bottom: 24px; }
        section h2 {
            font-size: 0.95rem; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.06em;
            margin: 0 0 8px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 10px;
            margin-bottom: 16px;
        }
        .stat {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 12px;
        }
        .stat .v {
            font-size: 1.4rem; font-weight: 600; color: var(--accent);
        }
        .stat .k {
            font-size: 0.75rem; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.04em;
        }
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
        tr:hover { background: rgba(167,139,250,0.04); }
        .badge {
            display: inline-block; padding: 2px 8px;
            border-radius: 4px; font-size: 0.75rem; font-weight: 500;
        }
        .badge-ok { background: rgba(34,197,94,0.15); color: var(--ok); }
        .badge-warn { background: rgba(250,204,21,0.15); color: var(--warn); }
        .badge-err { background: rgba(239,68,68,0.15); color: var(--err); }
        .badge-muted { background: rgba(255,255,255,0.06); color: var(--text-muted); }
        button, input {
            font-family: inherit;
            font-size: 0.85rem;
        }
        button {
            background: rgba(167,139,250,0.1);
            border: 1px solid var(--accent);
            color: var(--accent);
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 4px;
        }
        button:hover { background: rgba(167,139,250,0.2); }
        button.danger { border-color: var(--err); color: var(--err); background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        input[type="text"], input[type="number"] {
            background: #0a0a0a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 6px 10px;
            border-radius: 4px;
            margin-right: 6px;
        }
        input[type="text"]:focus, input[type="number"]:focus {
            outline: none; border-color: var(--accent);
        }
        .add-form { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
        .err-banner {
            color: var(--err); padding: 10px 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px;
            margin-bottom: 12px;
        }
        .empty {
            color: var(--text-muted); padding: 12px; text-align: center;
            font-style: italic;
        }
    </style>
</head>
<body>
    <header>
        <h1>🤫 Krab · Silence Admin</h1>
        <div class="meta">Polling 15s · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="err-banner"></div>

        <section>
            <h2>Сводка</h2>
            <div id="stats" class="stats-grid"></div>
        </section>

        <section>
            <h2>Добавить тишину</h2>
            <div class="card">
                <div class="add-form">
                    <input id="add-chat-id" type="text" placeholder="chat_id (например, -1001234567890)">
                    <input id="add-duration" type="number" min="1" max="10080" placeholder="минут (default 30)">
                    <input id="add-reason" type="text" placeholder="reason (опционально)">
                    <button id="add-btn">+ Заглушить</button>
                </div>
            </div>
        </section>

        <section>
            <h2>Активные silence</h2>
            <div id="active-container"></div>
        </section>

        <section>
            <h2>Расписание</h2>
            <div id="schedule-container" class="card"></div>
        </section>
    </main>
    <script>
        async function apiCall(method, url, body) {
            const opts = { method: method, headers: {} };
            if (body !== undefined) {
                opts.headers['Content-Type'] = 'application/json';
                opts.body = JSON.stringify(body);
            }
            const res = await fetch(url, opts);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
            return data;
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
        function fmtAge(iso) {
            if (!iso) return null;
            try {
                const d = new Date(iso);
                const ageSec = Math.floor((Date.now() - d.getTime()) / 1000);
                if (ageSec < 0) {
                    const a = Math.abs(ageSec);
                    if (a < 60) return 'in ' + a + 's';
                    if (a < 3600) return 'in ' + Math.floor(a / 60) + 'm';
                    return 'in ' + Math.floor(a / 3600) + 'h';
                }
                if (ageSec < 60) return ageSec + 's ago';
                if (ageSec < 3600) return Math.floor(ageSec / 60) + 'm ago';
                if (ageSec < 86400) return Math.floor(ageSec / 3600) + 'h ago';
                return Math.floor(ageSec / 86400) + 'd ago';
            } catch (e) { return iso; }
        }

        async function removeChat(chatId) {
            if (!confirm('Снять silence с ' + chatId + '?')) return;
            try {
                await apiCall('POST', '/api/admin/silence/remove', { chat_id: chatId });
                fetchData();
            } catch (e) {
                showError('Не удалось снять silence: ' + e.message);
            }
        }

        async function addSilence() {
            const chatId = document.getElementById('add-chat-id').value.trim();
            if (!chatId) {
                showError('chat_id обязателен');
                return;
            }
            const durationRaw = document.getElementById('add-duration').value.trim();
            const reason = document.getElementById('add-reason').value.trim();
            const body = { chat_id: chatId };
            if (durationRaw) body.duration_minutes = parseInt(durationRaw, 10);
            if (reason) body.reason = reason;
            try {
                await apiCall('POST', '/api/admin/silence/add', body);
                document.getElementById('add-chat-id').value = '';
                document.getElementById('add-duration').value = '';
                document.getElementById('add-reason').value = '';
                fetchData();
            } catch (e) {
                showError('Не удалось добавить silence: ' + e.message);
            }
        }

        function showError(msg) {
            const banner = document.getElementById('err-banner');
            const div = document.createElement('div');
            div.className = 'err-banner';
            div.textContent = msg;
            while (banner.firstChild) banner.removeChild(banner.firstChild);
            banner.appendChild(div);
        }

        function renderStats(stats) {
            const container = document.getElementById('stats');
            while (container.firstChild) container.removeChild(container.firstChild);
            const tiles = [
                { k: 'silenced_now', label: 'Silenced сейчас' },
                { k: 'active_per_chat', label: 'Per-chat активных' },
                { k: 'global_muted', label: 'Глобальный mute' },
                { k: 'scheduled_enabled', label: 'Расписание' },
            ];
            for (const t of tiles) {
                const card = document.createElement('div');
                card.className = 'stat';
                const v = document.createElement('div');
                v.className = 'v';
                let val = stats[t.k];
                if (typeof val === 'boolean') val = val ? 'ВКЛ' : 'ВЫКЛ';
                if (val === null || val === undefined) val = '—';
                v.textContent = String(val);
                const k = document.createElement('div');
                k.className = 'k';
                k.textContent = t.label;
                card.appendChild(v);
                card.appendChild(k);
                container.appendChild(card);
            }
            if (stats.scheduled_window) {
                const card = document.createElement('div');
                card.className = 'stat';
                const v = document.createElement('div');
                v.className = 'v';
                v.textContent = stats.scheduled_window;
                const k = document.createElement('div');
                k.className = 'k';
                k.textContent = 'Окно (UTC локальное)';
                card.appendChild(v);
                card.appendChild(k);
                container.appendChild(card);
            }
        }

        function renderActive(active) {
            const container = document.getElementById('active-container');
            while (container.firstChild) container.removeChild(container.firstChild);
            if (!active || active.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'card empty';
                empty.textContent = 'Активных silence нет.';
                container.appendChild(empty);
                return;
            }
            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            ['Chat ID', 'Since', 'Expiry', 'Осталось', 'Reason', 'Actions'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            });
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const a of active) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(a.chat_id || '', 'mono'));
                tr.appendChild(mkCell(fmtAge(a.since_iso) || '—'));
                tr.appendChild(mkCell(fmtAge(a.expiry_iso) || '—'));
                const remMin = a.remaining_min !== undefined ? a.remaining_min : null;
                tr.appendChild(mkCell(remMin !== null ? (remMin + ' мин') : '—'));
                tr.appendChild(mkCell(a.reason || '—'));
                const actTd = document.createElement('td');
                const btn = document.createElement('button');
                btn.className = 'danger';
                btn.textContent = '✕ снять';
                btn.addEventListener('click', () => removeChat(a.chat_id));
                actTd.appendChild(btn);
                tr.appendChild(actTd);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            container.appendChild(table);
        }

        function renderSchedule(scheduled) {
            const container = document.getElementById('schedule-container');
            while (container.firstChild) container.removeChild(container.firstChild);
            if (!scheduled || !scheduled.enabled) {
                container.textContent = 'Расписание выключено.';
                return;
            }
            const div = document.createElement('div');
            const title = document.createElement('div');
            const titleStrong = document.createElement('strong');
            titleStrong.textContent = (scheduled.start || '—') + ' – ' + (scheduled.end || '—');
            title.appendChild(titleStrong);
            div.appendChild(title);
            const status = document.createElement('div');
            status.style.marginTop = '6px';
            if (scheduled.active_now) {
                status.appendChild(mkBadge('сейчас активно', 'badge-ok'));
            } else {
                status.appendChild(mkBadge('ожидание окна', 'badge-muted'));
            }
            div.appendChild(status);
            const note = document.createElement('div');
            note.style.marginTop = '10px';
            note.style.color = 'var(--text-muted)';
            note.style.fontSize = '0.8rem';
            note.textContent = 'Расписание редактируется через команду !тишина расписание HH:MM HH:MM';
            div.appendChild(note);
            container.appendChild(div);
        }

        async function fetchData() {
            document.getElementById('err-banner').textContent = '';
            try {
                const data = await apiCall('GET', '/api/admin/silence/list');
                renderStats(data.stats || {});
                renderActive(data.active || []);
                renderSchedule(data.scheduled || {});
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                showError('Ошибка загрузки: ' + e.message);
            }
        }

        document.getElementById('add-btn').addEventListener('click', addSilence);
        fetchData();
        setInterval(fetchData, 15000);
    </script>
</body>
</html>
"""
