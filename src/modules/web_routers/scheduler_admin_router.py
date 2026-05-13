# -*- coding: utf-8 -*-
"""
Scheduler admin router — Wave 218.

Owner-side панель для просмотра отложенных Telegram-сообщений
(``src.core.message_scheduler``) и рекуррентных свёрм-задач
(``src.core.swarm_scheduler``). Эндпоинты возвращают только метаданные —
тексты сообщений усекаются до preview (50 chars).

Endpoints:
- GET  /api/admin/scheduler/list                 — JSON со списками.
- POST /api/admin/scheduler/cancel/{record_id}   — отмена scheduled message.
- GET  /admin/scheduler                          — HTML страница (polling 30s).

Контракт безопасности:
- list — read-only, без auth.
- cancel — ``ctx.assert_write_access_fn(...)`` (header X-Krab-Web-Key или
  query ``token=...``), плюс record_id sanitize.

XSS-safe: клиентский JS строит DOM через ``textContent`` (не innerHTML),
тексты сообщений усекаются на сервере. Без auto-escape мы могли бы получить
HTML-инъекцию через message_text — поэтому всё в textContent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# Persisted state файлы — мы НЕ дёргаем модули core/* напрямую при listing
# (это enabled cross-process visibility: панель работает даже если userbot
# рестартует). Cancel идёт через store API (mark_cancelled), плюс best-effort
# delete_messages через Pyrogram.
_SWARM_SCHED_STATE = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_recurring_jobs.json"

# Защита record_id (uuid hex slice, 8 chars) — только hex.
_RECORD_ID_PATTERN = re.compile(r"^[A-Fa-f0-9]{4,32}$")

# Максимум записей "completed/cancelled history".
_HISTORY_LIMIT = 50

# Сколько символов message_text оставлять для UI.
_PREVIEW_LEN = 50


def _validate_record_id(record_id: str) -> str:
    """Sanitize record_id: только hex + length guard."""
    record_id = (record_id or "").strip()
    if not record_id or not _RECORD_ID_PATTERN.match(record_id):
        raise HTTPException(status_code=400, detail="scheduler_invalid_record_id")
    return record_id


def _preview(text: str, *, limit: int = _PREVIEW_LEN) -> str:
    """Безопасный preview сообщения — обрезает по `limit` chars + ellipsis."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _iso_to_epoch(iso: str) -> float | None:
    """Парсит ISO-строку в epoch seconds. Возвращает None на ошибку."""
    iso = str(iso or "").strip()
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _load_messages() -> dict[str, list[dict[str, Any]]]:
    """Читает scheduled.json из MessageSchedulerStore singleton.

    Возвращает разделение на active / history:
    - active: status == "pending"
    - history: cancelled/sent/unknown — последние ``_HISTORY_LIMIT``,
      отсортированы по created_at desc.
    """
    try:
        from src.core.message_scheduler import msg_scheduler_store
    except Exception as exc:  # noqa: BLE001
        _logger.warning("scheduler_admin.import_msg_store_failed", error=str(exc))
        return {"active": [], "history": []}

    path = msg_scheduler_store.storage_path
    if not path.exists():
        return {"active": [], "history": []}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("records") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            return {"active": [], "history": []}
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("scheduler_admin.messages_read_failed", error=str(exc))
        return {"active": [], "history": []}

    active: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "pending")
        record = {
            "record_id": str(item.get("record_id") or ""),
            "chat_id": str(item.get("chat_id") or ""),
            "text_preview": _preview(item.get("text") or ""),
            "text_len": len(str(item.get("text") or "")),
            "schedule_time_iso": str(item.get("schedule_time_iso") or ""),
            "schedule_epoch": _iso_to_epoch(item.get("schedule_time_iso") or ""),
            "tg_message_id": int(item.get("tg_message_id") or 0),
            "created_at_iso": str(item.get("created_at_iso") or ""),
            "status": status,
        }
        if status == "pending":
            active.append(record)
        else:
            history.append(record)

    active.sort(key=lambda r: r["schedule_time_iso"])
    history.sort(key=lambda r: r["created_at_iso"], reverse=True)
    return {"active": active, "history": history[:_HISTORY_LIMIT]}


def _load_swarm_jobs() -> list[dict[str, Any]]:
    """Читает swarm_recurring_jobs.json (см. ``src.core.swarm_scheduler``).

    Файл может отсутствовать — возвращаем пустой список. Каждый job
    содержит team / topic / interval_sec / next_run_at / total_runs / enabled.
    """
    if not _SWARM_SCHED_STATE.exists():
        return []
    try:
        data = json.loads(_SWARM_SCHED_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("scheduler_admin.swarm_jobs_read_failed", error=str(exc))
        return []

    jobs_raw = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs_raw, list):
        return []

    result: list[dict[str, Any]] = []
    for item in jobs_raw:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "job_id": str(item.get("job_id") or ""),
                "team": str(item.get("team") or ""),
                "topic_preview": _preview(item.get("topic") or "", limit=80),
                "interval_sec": int(item.get("interval_sec") or 0),
                "workflow_type": str(item.get("workflow_type") or "standard"),
                "created_at": str(item.get("created_at") or ""),
                "last_run_at": str(item.get("last_run_at") or ""),
                "next_run_at": str(item.get("next_run_at") or ""),
                "next_run_epoch": _iso_to_epoch(item.get("next_run_at") or ""),
                "total_runs": int(item.get("total_runs") or 0),
                "last_error": _preview(item.get("last_error") or "", limit=120),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    result.sort(key=lambda j: (not j["enabled"], j["next_run_at"] or "9999"))
    return result


async def _try_delete_tg_message(
    ctx: RouterContext,
    *,
    chat_id: str,
    tg_message_id: int,
) -> tuple[bool, str]:
    """Best-effort delete scheduled message в Telegram через Pyrogram.

    Не raises — если бот недоступен или delete падает, мы просто помечаем
    запись cancelled локально (как в !schedule cancel). Возвращает
    ``(deleted, warn)``.
    """
    if tg_message_id <= 0:
        return False, "no_tg_message_id"
    bot = ctx.get_dep("kraab_userbot") or ctx.get_dep("userbot") or ctx.get_dep("bot")
    if bot is None or not hasattr(bot, "client") or bot.client is None:
        return False, "userbot_unavailable"
    try:
        await bot.client.delete_messages(
            chat_id=int(chat_id),
            message_ids=tg_message_id,
            revoke=True,
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "scheduler_admin.tg_delete_failed",
            chat_id=chat_id,
            tg_message_id=tg_message_id,
            error=str(exc),
        )
        return False, f"tg_delete_failed: {exc}"


def build_scheduler_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: APIRouter для /admin/scheduler.

    Подключается в WebApp через include_router(...). Эндпоинты не зависят
    от RouterContext rate_state — они read-mostly.
    """
    router = APIRouter(tags=["scheduler-admin"])

    # ── GET /api/admin/scheduler/list ───────────────────────────────────────

    @router.get("/api/admin/scheduler/list")
    async def scheduler_list() -> dict[str, Any]:
        """Возвращает active scheduled messages + history + swarm jobs."""
        try:
            messages = _load_messages()
            swarm_jobs = _load_swarm_jobs()
        except Exception as exc:  # noqa: BLE001
            _logger.error("scheduler_admin.list_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"scheduler_list_failed: {exc}",
            ) from exc

        return {
            "ok": True,
            "messages": messages["active"],
            "messages_history": messages["history"],
            "messages_active_count": len(messages["active"]),
            "messages_history_count": len(messages["history"]),
            "swarm_jobs": swarm_jobs,
            "swarm_jobs_count": len(swarm_jobs),
            "now_epoch": datetime.now(timezone.utc).timestamp(),
        }

    # ── POST /api/admin/scheduler/cancel/{record_id} ────────────────────────

    @router.post("/api/admin/scheduler/cancel/{record_id}")
    async def scheduler_cancel(
        record_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Отменяет scheduled message: best-effort TG delete + локальный
        mark_cancelled в MessageSchedulerStore."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        record_id = _validate_record_id(record_id)

        try:
            from src.core.message_scheduler import msg_scheduler_store
        except Exception as exc:  # noqa: BLE001
            _logger.error("scheduler_admin.cancel_import_failed", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail=f"scheduler_cancel_import_failed: {exc}",
            ) from exc

        rec = msg_scheduler_store.get(record_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="scheduler_record_not_found")
        if rec.status != "pending":
            return {
                "ok": True,
                "record_id": record_id,
                "already": rec.status,
                "tg_deleted": False,
                "warning": f"already_{rec.status}",
            }

        tg_deleted, warn = await _try_delete_tg_message(
            ctx,
            chat_id=rec.chat_id,
            tg_message_id=rec.tg_message_id,
        )
        marked = msg_scheduler_store.mark_cancelled(record_id)
        _logger.info(
            "scheduler_admin.cancel",
            record_id=record_id,
            tg_deleted=tg_deleted,
            marked=marked,
        )
        return {
            "ok": True,
            "record_id": record_id,
            "tg_deleted": tg_deleted,
            "marked": marked,
            "warning": warn,
        }

    # ── GET /admin/scheduler ────────────────────────────────────────────────

    @router.get("/admin/scheduler", response_class=HTMLResponse)
    async def scheduler_admin_page() -> HTMLResponse:
        """HTML страница со списком scheduled messages + swarm jobs."""
        return HTMLResponse(_SCHEDULER_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/scheduler ───────────────────────────────────────────
# Стиль матчит cron_admin_router.py. JS строит DOM через textContent (XSS-safe).

_SCHEDULER_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Scheduler Admin</title>
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
        h2 {
            font-size: 1rem; color: var(--text-muted);
            margin: 24px 0 8px; text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        h2:first-of-type { margin-top: 0; }
        table {
            width: 100%; border-collapse: collapse;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px; overflow: hidden;
            font-size: 0.9rem;
            margin-bottom: 8px;
        }
        th, td {
            padding: 8px 10px; text-align: left;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
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
        .summary { color: var(--text-muted); margin-bottom: 12px; font-size: 0.85rem; }
        .err-banner { color: var(--err); padding: 12px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 12px; }
        .empty { color: var(--text-muted); padding: 16px;
            background: var(--card-bg); border: 1px dashed var(--border);
            border-radius: 6px; text-align: center; font-size: 0.85rem; }
        .text-preview { color: var(--text); font-size: 0.85rem; max-width: 360px; }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Scheduler Admin</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="summary" class="summary">Загружаем…</div>
        <div id="err-banner"></div>

        <h2>📅 Активные отложенные сообщения</h2>
        <div id="messages-section"></div>

        <h2>🤖 Свёрм-задачи (рекуррентные)</h2>
        <div id="swarm-section"></div>

        <h2>🗂️ История (отменённые / отправленные)</h2>
        <div id="history-section"></div>
    </main>
    <script>
        async function callAdmin(method, url) {
            try {
                const res = await fetch(url, { method: method });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
                return data;
            } catch (e) {
                alert('Ошибка: ' + e.message);
                throw e;
            }
        }
        async function cancelMessage(recordId) {
            if (!confirm('Отменить запланированное сообщение ' + recordId + '?')) return;
            await callAdmin('POST', '/api/admin/scheduler/cancel/' + encodeURIComponent(recordId));
            fetchData();
        }
        function fmtCountdown(epoch, nowEpoch) {
            if (!epoch || !nowEpoch) return '—';
            const diff = Math.floor(epoch - nowEpoch);
            if (diff < 0) {
                const past = -diff;
                if (past < 60) return past + 's назад';
                if (past < 3600) return Math.floor(past / 60) + 'm назад';
                return Math.floor(past / 3600) + 'h назад';
            }
            if (diff < 60) return 'через ' + diff + 's';
            if (diff < 3600) return 'через ' + Math.floor(diff / 60) + 'm';
            if (diff < 86400) return 'через ' + Math.floor(diff / 3600) + 'h';
            return 'через ' + Math.floor(diff / 86400) + 'd';
        }
        function fmtInterval(sec) {
            if (!sec) return '—';
            if (sec >= 86400 && sec % 86400 === 0) return (sec / 86400) + 'd';
            if (sec >= 3600 && sec % 3600 === 0) return (sec / 3600) + 'h';
            if (sec >= 60 && sec % 60 === 0) return (sec / 60) + 'm';
            return sec + 's';
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
        function mkCell(content, mono) {
            const td = document.createElement('td');
            if (mono) td.className = 'mono';
            if (typeof content === 'string') td.textContent = content;
            else if (content instanceof Node) td.appendChild(content);
            return td;
        }
        function mkEmpty(parent, msg) {
            const div = document.createElement('div');
            div.className = 'empty';
            div.textContent = msg;
            parent.appendChild(div);
        }
        function renderMessagesTable(records, nowEpoch) {
            const section = document.getElementById('messages-section');
            while (section.firstChild) section.removeChild(section.firstChild);
            if (!records || records.length === 0) {
                mkEmpty(section, 'Нет активных отложенных сообщений.');
                return;
            }
            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            ['ID', 'Чат', 'Текст', 'Время', 'Осталось', 'TG msg', 'Действия'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            });
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const rec of records) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(rec.record_id, true));
                tr.appendChild(mkCell(rec.chat_id, true));
                const textTd = document.createElement('td');
                textTd.className = 'text-preview';
                textTd.textContent = rec.text_preview + (rec.text_len > rec.text_preview.length ? ' …' : '');
                textTd.title = 'Длина: ' + rec.text_len + ' chars';
                tr.appendChild(textTd);
                tr.appendChild(mkCell(rec.schedule_time_iso || '—', true));
                tr.appendChild(mkCell(fmtCountdown(rec.schedule_epoch, nowEpoch)));
                tr.appendChild(mkCell(String(rec.tg_message_id || '—'), true));
                const actTd = document.createElement('td');
                actTd.appendChild(mkButton('🗑️ Отменить',
                    () => cancelMessage(rec.record_id), 'danger'));
                tr.appendChild(actTd);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            section.appendChild(table);
        }
        function renderSwarmTable(jobs, nowEpoch) {
            const section = document.getElementById('swarm-section');
            while (section.firstChild) section.removeChild(section.firstChild);
            if (!jobs || jobs.length === 0) {
                mkEmpty(section, 'Нет рекуррентных свёрм-задач.');
                return;
            }
            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            ['ID', 'Команда', 'Тема', 'Интервал', 'Workflow', 'Прогонов',
             'Last run', 'Next run', 'Status'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            });
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const job of jobs) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(job.job_id, true));
                tr.appendChild(mkCell(job.team, true));
                const topicTd = document.createElement('td');
                topicTd.className = 'text-preview';
                topicTd.textContent = job.topic_preview;
                tr.appendChild(topicTd);
                tr.appendChild(mkCell(fmtInterval(job.interval_sec), true));
                tr.appendChild(mkCell(job.workflow_type, true));
                tr.appendChild(mkCell(String(job.total_runs), true));
                tr.appendChild(mkCell(job.last_run_at || '—', true));
                const nextTd = document.createElement('td');
                nextTd.className = 'mono';
                nextTd.textContent = job.next_run_at || '—';
                if (job.next_run_epoch) {
                    nextTd.title = fmtCountdown(job.next_run_epoch, nowEpoch);
                }
                tr.appendChild(nextTd);
                const stTd = document.createElement('td');
                if (job.last_error) {
                    stTd.appendChild(mkBadge('error', 'badge-err'));
                    stTd.title = job.last_error;
                } else if (job.enabled) {
                    stTd.appendChild(mkBadge('enabled', 'badge-ok'));
                } else {
                    stTd.appendChild(mkBadge('paused', 'badge-muted'));
                }
                tr.appendChild(stTd);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            section.appendChild(table);
        }
        function renderHistoryTable(records) {
            const section = document.getElementById('history-section');
            while (section.firstChild) section.removeChild(section.firstChild);
            if (!records || records.length === 0) {
                mkEmpty(section, 'История пуста.');
                return;
            }
            const table = document.createElement('table');
            const thead = document.createElement('thead');
            const trh = document.createElement('tr');
            ['ID', 'Чат', 'Текст', 'Plan time', 'Created', 'Status'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                trh.appendChild(th);
            });
            thead.appendChild(trh);
            table.appendChild(thead);
            const tbody = document.createElement('tbody');
            for (const rec of records) {
                const tr = document.createElement('tr');
                tr.appendChild(mkCell(rec.record_id, true));
                tr.appendChild(mkCell(rec.chat_id, true));
                const textTd = document.createElement('td');
                textTd.className = 'text-preview';
                textTd.textContent = rec.text_preview;
                tr.appendChild(textTd);
                tr.appendChild(mkCell(rec.schedule_time_iso || '—', true));
                tr.appendChild(mkCell(rec.created_at_iso || '—', true));
                const stTd = document.createElement('td');
                if (rec.status === 'cancelled') stTd.appendChild(mkBadge('cancelled', 'badge-warn'));
                else if (rec.status === 'sent') stTd.appendChild(mkBadge('sent', 'badge-ok'));
                else stTd.appendChild(mkBadge(rec.status, 'badge-muted'));
                tr.appendChild(stTd);
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
            section.appendChild(table);
        }
        async function fetchData() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/scheduler/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                const nowEpoch = data.now_epoch || (Date.now() / 1000);
                renderMessagesTable(data.messages || [], nowEpoch);
                renderSwarmTable(data.swarm_jobs || [], nowEpoch);
                renderHistoryTable(data.messages_history || []);
                const summary = document.getElementById('summary');
                while (summary.firstChild) summary.removeChild(summary.firstChild);
                summary.appendChild(document.createTextNode(
                    'Активных сообщений: ' + (data.messages_active_count || 0) +
                    ' · Свёрм-задач: ' + (data.swarm_jobs_count || 0) +
                    ' · История: ' + (data.messages_history_count || 0)
                ));
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка загрузки: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        fetchData();
        setInterval(fetchData, 30000);
    </script>
</body>
</html>
"""
