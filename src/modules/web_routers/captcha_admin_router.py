# -*- coding: utf-8 -*-
"""
Captcha / Spam Guard admin router — Wave 220.

Owner-side панель для управления антиспамом и captcha-контролями. Все
данные читаются из существующих storage-файлов (`spam_filter_config.json`,
структурных логов krab_main.log) — этот роутер ничего не пишет вне своих
двух state-файлов (`spam_whitelist.json` и `spam_banned_users.json`).

Endpoints (READY):
- GET  /api/admin/captcha/list           — recent spam catches + stats + settings.
- POST /api/admin/captcha/whitelist/add  — добавить user_id/username в whitelist.
- POST /api/admin/captcha/whitelist/remove — убрать из whitelist.
- POST /api/admin/captcha/ban/{user_id}  — global ban пользователя.
- POST /api/admin/captcha/unban/{user_id} — снять глобальный ban.
- POST /api/admin/captcha/reset/{chat_id} — сбросить in-memory flood-стейт чата.
- GET  /admin/captcha                     — HTML страница (polling 30s).

Контракт безопасности: write-endpoints идут через
``ctx.assert_write_access_fn``, ID-параметры sanitize regex'ом, тексты
сообщений в payload отдаются обрезанными до 30 символов с XSS-safe
рендерингом на клиенте (textContent / createElement, без innerHTML).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.core.logger import get_logger

from ._context import RouterContext

_logger = get_logger(__name__)

# ── State paths ──────────────────────────────────────────────────────────────

# Конфиг спам-гарда (per-chat enabled/action) — пишется через src.core.spam_guard.
_SPAM_CONFIG_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "spam_filter_config.json"

# Whitelist trusted users (bypass captcha/spam). Управляется только этим роутером.
_WHITELIST_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "spam_whitelist.json"

# Globally-banned users. Read-only списком + owner panel toggle (write).
_BANNED_USERS_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "spam_banned_users.json"

# Основной structlog-файл (источник recent spam catches).
_DEFAULT_LOG_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "krab_main.log"

# ── Limits / constants ───────────────────────────────────────────────────────

# Сколько последних catches показывать.
_RECENT_LIMIT = 50

# Максимум байт читать с конца лога (защита от больших файлов).
_MAX_SCAN_BYTES = 4 * 1024 * 1024  # 4 MiB

# Длина превью текста сообщения (PII protection — не выдаём полные тексты).
_PREVIEW_MAX_CHARS = 30

# Окно для daily-stats: 7 суток назад.
_STATS_WINDOW_DAYS = 7

# Sanitize patterns.
_ID_PATTERN = re.compile(r"^-?\d{1,20}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,32}$")


# ── Pydantic схемы ───────────────────────────────────────────────────────────


class WhitelistAddRequest(BaseModel):
    """Запрос на добавление в whitelist."""

    user_id: int | None = Field(default=None, description="Telegram user_id")
    username: str | None = Field(default=None, description="Telegram username без @")
    note: str = Field(default="", max_length=200, description="Комментарий администратора")


class WhitelistRemoveRequest(BaseModel):
    """Запрос на удаление из whitelist."""

    user_id: int | None = None
    username: str | None = None


# ── State I/O ────────────────────────────────────────────────────────────────


def _load_json(path: Path, default: Any) -> Any:
    """Безопасно читает JSON. При ошибке возвращает default."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("captcha_admin.read_failed", path=str(path), error=str(exc))
    return default


def _save_json(path: Path, data: Any) -> None:
    """Атомарная запись JSON (mkdir + write tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_whitelist() -> dict[str, dict[str, Any]]:
    """Возвращает whitelist в виде {key: {user_id, username, note, added_ts}}.

    Ключ — это user_id (строкой) если задан, иначе username (lowercased).
    """
    data = _load_json(_WHITELIST_PATH, default={})
    if not isinstance(data, dict):
        return {}
    return data


def _load_banned() -> dict[str, dict[str, Any]]:
    """Возвращает banned-users {user_id: {note, banned_ts}}."""
    data = _load_json(_BANNED_USERS_PATH, default={})
    if not isinstance(data, dict):
        return {}
    return data


# ── Sanitize ─────────────────────────────────────────────────────────────────


def _validate_user_id(value: int | str) -> int:
    """Проверяет user_id (отрицательные допустимы для chat_id-форматов, но
    здесь это user — лимитируем диапазоном Telegram до 2^63)."""
    raw = str(value).strip()
    if not _ID_PATTERN.match(raw):
        raise HTTPException(status_code=400, detail="captcha_invalid_user_id")
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="captcha_invalid_user_id") from exc
    if parsed == 0:
        raise HTTPException(status_code=400, detail="captcha_user_id_zero")
    return parsed


def _validate_username(value: str) -> str:
    """Лимитируем username Telegram-правилами."""
    raw = (value or "").strip().lstrip("@")
    if not _USERNAME_PATTERN.match(raw):
        raise HTTPException(status_code=400, detail="captcha_invalid_username")
    return raw.lower()


def _validate_chat_id(value: int | str) -> str:
    """Sanitize chat_id (для reset-флоудстейта)."""
    raw = str(value).strip()
    if not _ID_PATTERN.match(raw):
        raise HTTPException(status_code=400, detail="captcha_invalid_chat_id")
    return raw


# ── PII-safe сокращение текста ───────────────────────────────────────────────


def _sanitize_preview(text: str) -> str:
    """Обрезаем текст сообщения до _PREVIEW_MAX_CHARS, убираем переносы строк."""
    if not text:
        return ""
    cleaned = " ".join(text.split())  # схлопываем whitespace
    if len(cleaned) <= _PREVIEW_MAX_CHARS:
        return cleaned
    return cleaned[:_PREVIEW_MAX_CHARS] + "…"


# ── Log scan: recent spam catches ────────────────────────────────────────────


def _resolve_log_path() -> Path | None:
    """Возвращает путь к structlog-файлу Krab (re-use логика logger.py)."""
    raw = os.environ.get("KRAB_LOG_FILE")
    if raw is not None:
        if raw == "" or raw.lower() == "none":
            return None
        return Path(raw).expanduser()
    base = os.environ.get("KRAB_RUNTIME_STATE_DIR")
    base_dir = Path(base).expanduser() if base else Path.home() / ".openclaw" / "krab_runtime_state"
    return base_dir / "krab_main.log"


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Парсит одну structlog-строку (JSON), возвращает dict или None."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _is_spam_event(parsed: dict[str, Any]) -> bool:
    """True если это log-событие про spam_detected."""
    event = parsed.get("event") or ""
    return event == "spam_detected"


def _extract_catch(parsed: dict[str, Any]) -> dict[str, Any]:
    """Достаёт нужные поля из structlog-записи + sanitizes preview."""
    return {
        "ts": parsed.get("timestamp") or parsed.get("ts"),
        "chat_id": str(parsed.get("chat_id") or ""),
        "user_id": str(parsed.get("user_id") or ""),
        "reason": str(parsed.get("reason") or ""),
        "action": str(parsed.get("action") or ""),
        "preview": _sanitize_preview(str(parsed.get("preview") or parsed.get("text") or "")),
    }


def _read_recent_spam_events(
    path: Path,
    *,
    limit: int = _RECENT_LIMIT,
    max_scan_bytes: int = _MAX_SCAN_BYTES,
) -> tuple[list[dict[str, Any]], int]:
    """Reverse-сканирует structlog-файл и возвращает (catches, total_seen)."""
    if not path.exists():
        return [], 0
    try:
        size = path.stat().st_size
    except OSError as exc:
        _logger.warning("captcha_admin.log_stat_failed", path=str(path), error=str(exc))
        return [], 0
    if size == 0:
        return [], 0

    scan_limit = min(size, max_scan_bytes)
    catches: list[dict[str, Any]] = []
    chunk_size = 64 * 1024
    buffer = b""
    scanned = 0

    try:
        with open(path, "rb") as fp:
            pos = size
            while pos > 0 and scanned < scan_limit and len(catches) < limit:
                read_size = min(chunk_size, pos, scan_limit - scanned)
                pos -= read_size
                fp.seek(pos)
                chunk = fp.read(read_size)
                scanned += read_size
                buffer = chunk + buffer
                lines = buffer.split(b"\n")
                # Самая левая может быть частичной — оставляем её в буфере.
                buffer = lines[0]
                # Идём с конца — последние строки первыми.
                for raw_line in reversed(lines[1:]):
                    try:
                        text = raw_line.decode("utf-8", errors="replace")
                    except UnicodeDecodeError:
                        continue
                    parsed = _parse_log_line(text)
                    if parsed is None:
                        continue
                    if _is_spam_event(parsed):
                        catches.append(_extract_catch(parsed))
                        if len(catches) >= limit:
                            break
            # Не забываем последнюю накопленную строку.
            if len(catches) < limit and buffer:
                parsed = _parse_log_line(buffer.decode("utf-8", errors="replace"))
                if parsed and _is_spam_event(parsed):
                    catches.append(_extract_catch(parsed))
    except OSError as exc:
        _logger.warning("captcha_admin.log_read_failed", error=str(exc))
        return catches, len(catches)

    return catches, len(catches)


# ── Stats: catches per day (7d) ──────────────────────────────────────────────


def _compute_daily_stats(catches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Возвращает per-day счётчики catches за последние _STATS_WINDOW_DAYS."""
    now = datetime.now(timezone.utc)
    counter: Counter[str] = Counter()
    for catch in catches:
        ts = catch.get("ts")
        if not ts:
            continue
        try:
            # Pyrofork timestamp может быть ISO-строкой или epoch.
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        delta_days = (now - dt).days
        if delta_days >= _STATS_WINDOW_DAYS or delta_days < 0:
            continue
        counter[dt.strftime("%Y-%m-%d")] += 1
    # Возвращаем массив за 7 дней (включая сегодня), включая нулевые.
    out: list[dict[str, Any]] = []
    for i in range(_STATS_WINDOW_DAYS - 1, -1, -1):
        day = now.timestamp() - i * 86400
        key = datetime.fromtimestamp(day, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": key, "count": int(counter.get(key, 0))})
    return out


# ── Captcha settings aggregation ─────────────────────────────────────────────


def _load_captcha_settings() -> dict[str, Any]:
    """Возвращает агрегированные настройки spam_guard для всех чатов."""
    cfg = _load_json(_SPAM_CONFIG_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    chats: list[dict[str, Any]] = []
    active_count = 0
    for raw_chat_id, entry in cfg.items():
        if not isinstance(entry, dict):
            continue
        enabled = bool(entry.get("enabled", False))
        action = str(entry.get("action", "delete"))
        if enabled:
            active_count += 1
        chats.append(
            {
                "chat_id": str(raw_chat_id),
                "enabled": enabled,
                "action": action,
            }
        )
    chats.sort(key=lambda x: x["chat_id"])
    return {
        # Глобальный "active" — есть ли хотя бы один чат с enabled=true.
        "active": active_count > 0,
        "active_chats_count": active_count,
        "total_chats_count": len(chats),
        "chats": chats,
        # Жёсткие константы из src/core/spam_guard.py.
        "flood_msg_limit": 5,
        "flood_window_sec": 10.0,
        "link_limit": 3,
    }


# ── Router factory ───────────────────────────────────────────────────────────


def build_captcha_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с captcha/spam-guard endpoints."""
    router = APIRouter(tags=["captcha-admin"])

    # ── GET /api/admin/captcha/list ─────────────────────────────────────────

    @router.get("/api/admin/captcha/list")
    async def captcha_list() -> dict:
        """Возвращает recent catches + статистику + whitelist + ban-list + settings."""
        log_path = _resolve_log_path() or _DEFAULT_LOG_FILE
        catches, _seen = _read_recent_spam_events(log_path, limit=_RECENT_LIMIT)
        whitelist = _load_whitelist()
        banned = _load_banned()
        settings = _load_captcha_settings()

        return {
            "ok": True,
            "count_recent": len(catches),
            "recent": catches,
            "daily_stats": _compute_daily_stats(catches),
            "whitelist": list(whitelist.values()),
            "whitelist_count": len(whitelist),
            "banned_users": list(banned.values()),
            "banned_count": len(banned),
            "settings": settings,
        }

    # ── POST /api/admin/captcha/whitelist/add ───────────────────────────────

    @router.post("/api/admin/captcha/whitelist/add")
    async def whitelist_add(
        payload: WhitelistAddRequest,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Добавляет user_id/username в whitelist (write-access)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        if payload.user_id is None and not payload.username:
            raise HTTPException(status_code=400, detail="captcha_whitelist_id_or_username_required")

        entry: dict[str, Any] = {
            "note": (payload.note or "").strip()[:200],
            "added_ts": time.time(),
        }
        if payload.user_id is not None:
            uid = _validate_user_id(payload.user_id)
            entry["user_id"] = uid
            key = str(uid)
        else:
            uname = _validate_username(payload.username or "")
            entry["username"] = uname
            key = uname

        wl = _load_whitelist()
        wl[key] = entry
        _save_json(_WHITELIST_PATH, wl)
        _logger.info("captcha_admin.whitelist_added", key=key, note=entry["note"])
        return {"ok": True, "key": key, "entry": entry}

    # ── POST /api/admin/captcha/whitelist/remove ────────────────────────────

    @router.post("/api/admin/captcha/whitelist/remove")
    async def whitelist_remove(
        payload: WhitelistRemoveRequest,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Удаляет user_id/username из whitelist (write-access)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        if payload.user_id is not None:
            key = str(_validate_user_id(payload.user_id))
        elif payload.username:
            key = _validate_username(payload.username)
        else:
            raise HTTPException(status_code=400, detail="captcha_whitelist_id_or_username_required")

        wl = _load_whitelist()
        if key not in wl:
            raise HTTPException(status_code=404, detail=f"captcha_whitelist_not_found: {key}")
        wl.pop(key, None)
        _save_json(_WHITELIST_PATH, wl)
        _logger.info("captcha_admin.whitelist_removed", key=key)
        return {"ok": True, "key": key}

    # ── POST /api/admin/captcha/ban/{user_id} ───────────────────────────────

    @router.post("/api/admin/captcha/ban/{user_id}")
    async def captcha_ban(
        user_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
        note: str = Query(default=""),
    ) -> dict:
        """Глобальный ban пользователя (write-access)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        uid = _validate_user_id(user_id)
        banned = _load_banned()
        entry = {
            "user_id": uid,
            "note": (note or "").strip()[:200],
            "banned_ts": time.time(),
        }
        banned[str(uid)] = entry
        _save_json(_BANNED_USERS_PATH, banned)
        _logger.info("captcha_admin.user_banned", user_id=uid, note=entry["note"])
        return {"ok": True, "user_id": uid, "entry": entry}

    # ── POST /api/admin/captcha/unban/{user_id} ─────────────────────────────

    @router.post("/api/admin/captcha/unban/{user_id}")
    async def captcha_unban(
        user_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Снимает global ban с пользователя (write-access)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        uid = _validate_user_id(user_id)
        banned = _load_banned()
        key = str(uid)
        if key not in banned:
            raise HTTPException(status_code=404, detail=f"captcha_banned_not_found: {key}")
        banned.pop(key, None)
        _save_json(_BANNED_USERS_PATH, banned)
        _logger.info("captcha_admin.user_unbanned", user_id=uid)
        return {"ok": True, "user_id": uid}

    # ── POST /api/admin/captcha/reset/{chat_id} ─────────────────────────────

    @router.post("/api/admin/captcha/reset/{chat_id}")
    async def captcha_reset(
        chat_id: str,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Сбрасывает in-memory flood-tracker для чата (write-access)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        key = _validate_chat_id(chat_id)
        try:
            from src.core.spam_guard import _flood_tracker  # noqa: PLC0415

            if key in _flood_tracker:
                _flood_tracker.pop(key, None)
                _logger.info("captcha_admin.flood_reset", chat_id=key)
                return {"ok": True, "chat_id": key, "reset": True}
            return {"ok": True, "chat_id": key, "reset": False, "note": "no_state_for_chat"}
        except Exception as exc:  # noqa: BLE001
            _logger.warning("captcha_admin.reset_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"captcha_reset_failed: {exc}") from exc

    # ── GET /admin/captcha — HTML page ──────────────────────────────────────

    @router.get("/admin/captcha", response_class=HTMLResponse)
    async def captcha_admin_page() -> HTMLResponse:
        """HTML страница со spam-каtchami, whitelist'ом и контролями."""
        return HTMLResponse(_CAPTCHA_ADMIN_PAGE_HTML)

    return router


# ── HTML страница /admin/captcha ─────────────────────────────────────────────
# XSS-safe: всё рендерим через createElement + textContent, никаких innerHTML
# с user-controlled данными. Превью текста уже обрезано на бэкенде до 30 chars.

_CAPTCHA_ADMIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Krab · Captcha Admin</title>
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
        main { padding: 16px 24px; display: grid; grid-template-columns: 1fr; gap: 16px; }
        section {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px 16px;
        }
        section h2 {
            margin: 0 0 10px;
            font-size: 1.05rem;
            color: var(--accent);
            font-weight: 500;
        }
        table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
        th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: #1a1a1a; color: var(--text-muted);
             text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.04em; }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: rgba(125,211,252,0.04); }
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
            font-size: 0.75rem;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 4px;
            font-family: inherit;
        }
        button:hover { background: rgba(125,211,252,0.2); }
        button.danger { border-color: var(--err); color: var(--err);
                         background: rgba(239,68,68,0.08); }
        button.danger:hover { background: rgba(239,68,68,0.18); }
        .summary { color: var(--text-muted); font-size: 0.85rem; }
        .err-banner { color: var(--err); padding: 10px;
            background: rgba(239,68,68,0.08); border-radius: 4px; margin-bottom: 10px; }
        input[type="text"], input[type="number"] {
            background: #1a1a1a; border: 1px solid var(--border);
            color: var(--text); padding: 4px 8px; border-radius: 4px;
            font-family: inherit; font-size: 0.85rem;
        }
        .form-row { display: flex; gap: 6px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
        .bar {
            display: inline-block; height: 16px; background: var(--accent);
            border-radius: 2px; vertical-align: middle; min-width: 2px;
        }
    </style>
</head>
<body>
    <header>
        <h1>🦀 Krab · Captcha &amp; Spam Guard</h1>
        <div class="meta">Polling каждые 30 сек · <span id="last-update">—</span></div>
    </header>
    <main>
        <div id="err-banner"></div>

        <section>
            <h2>📊 Settings &amp; Stats</h2>
            <div id="summary" class="summary">Загружаем…</div>
            <div id="daily-chart" style="margin-top:10px;"></div>
        </section>

        <section>
            <h2>🚨 Recent catches (50 last)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Chat</th>
                        <th>User</th>
                        <th>Reason</th>
                        <th>Action</th>
                        <th>Preview</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="catches-body"></tbody>
            </table>
        </section>

        <section>
            <h2>✅ Whitelist (trusted senders)</h2>
            <div class="form-row">
                <input id="wl-userid" type="number" placeholder="user_id">
                <input id="wl-username" type="text" placeholder="username (без @)">
                <input id="wl-note" type="text" placeholder="комментарий (опционально)">
                <button id="wl-add-btn">+ Добавить</button>
            </div>
            <table style="margin-top:10px;">
                <thead>
                    <tr><th>User ID</th><th>Username</th><th>Note</th><th>Added</th><th></th></tr>
                </thead>
                <tbody id="wl-body"></tbody>
            </table>
        </section>

        <section>
            <h2>🚫 Banned users</h2>
            <table>
                <thead>
                    <tr><th>User ID</th><th>Note</th><th>Banned at</th><th></th></tr>
                </thead>
                <tbody id="ban-body"></tbody>
            </table>
        </section>
    </main>
    <script>
        async function callAdmin(method, url, body) {
            try {
                const opts = { method: method };
                if (body) {
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
        function fmtAge(ts) {
            if (!ts) return '—';
            try {
                let dt;
                if (typeof ts === 'number') dt = new Date(ts * 1000);
                else dt = new Date(ts);
                const sec = Math.floor((Date.now() - dt.getTime()) / 1000);
                if (sec < 60) return sec + 's ago';
                if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
                if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
                return Math.floor(sec / 86400) + 'd ago';
            } catch (e) { return String(ts); }
        }
        function mkCell(text) {
            const td = document.createElement('td');
            td.textContent = text == null ? '' : String(text);
            return td;
        }
        function mkMonoCell(text) {
            const td = mkCell(text);
            td.className = 'mono';
            return td;
        }
        function mkBadge(text, cls) {
            const span = document.createElement('span');
            span.className = 'badge ' + cls;
            span.textContent = text;
            return span;
        }
        function mkButton(text, onClick, cls) {
            const b = document.createElement('button');
            b.textContent = text;
            if (cls) b.className = cls;
            b.addEventListener('click', onClick);
            return b;
        }

        async function banUser(userId) {
            if (!confirm('Ban user ' + userId + ' globally?')) return;
            await callAdmin('POST', '/api/admin/captcha/ban/' + encodeURIComponent(userId));
            refresh();
        }
        async function unbanUser(userId) {
            if (!confirm('Unban ' + userId + '?')) return;
            await callAdmin('POST', '/api/admin/captcha/unban/' + encodeURIComponent(userId));
            refresh();
        }
        async function resetChat(chatId) {
            if (!confirm('Reset captcha flood-state для chat ' + chatId + '?')) return;
            await callAdmin('POST', '/api/admin/captcha/reset/' + encodeURIComponent(chatId));
            refresh();
        }
        async function whitelistRemove(payload) {
            await callAdmin('POST', '/api/admin/captcha/whitelist/remove', payload);
            refresh();
        }

        document.getElementById('wl-add-btn').addEventListener('click', async () => {
            const uid = document.getElementById('wl-userid').value.trim();
            const uname = document.getElementById('wl-username').value.trim();
            const note = document.getElementById('wl-note').value.trim();
            const payload = { note: note };
            if (uid) payload.user_id = parseInt(uid, 10);
            if (uname) payload.username = uname;
            if (!uid && !uname) { alert('Нужен user_id или username.'); return; }
            await callAdmin('POST', '/api/admin/captcha/whitelist/add', payload);
            document.getElementById('wl-userid').value = '';
            document.getElementById('wl-username').value = '';
            document.getElementById('wl-note').value = '';
            refresh();
        });

        function renderCatches(catches) {
            const tbody = document.getElementById('catches-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const c of catches) {
                const tr = document.createElement('tr');
                tr.appendChild(mkMonoCell(fmtAge(c.ts)));
                tr.appendChild(mkMonoCell(c.chat_id || '—'));
                tr.appendChild(mkMonoCell(c.user_id || '—'));
                const reasonTd = document.createElement('td');
                reasonTd.appendChild(mkBadge(c.reason || '—', 'badge-warn'));
                tr.appendChild(reasonTd);
                const actTd = document.createElement('td');
                actTd.appendChild(mkBadge(c.action || '—',
                    c.action === 'ban' ? 'badge-err' :
                    c.action === 'mute' ? 'badge-warn' : 'badge-muted'));
                tr.appendChild(actTd);
                tr.appendChild(mkCell(c.preview || '—'));
                const actionsTd = document.createElement('td');
                if (c.user_id) {
                    actionsTd.appendChild(mkButton('🚫 ban',
                        () => banUser(c.user_id), 'danger'));
                }
                if (c.chat_id) {
                    actionsTd.appendChild(mkButton('↻ reset',
                        () => resetChat(c.chat_id)));
                }
                tr.appendChild(actionsTd);
                tbody.appendChild(tr);
            }
        }
        function renderWhitelist(items) {
            const tbody = document.getElementById('wl-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const e of items) {
                const tr = document.createElement('tr');
                tr.appendChild(mkMonoCell(e.user_id || '—'));
                tr.appendChild(mkMonoCell(e.username ? '@' + e.username : '—'));
                tr.appendChild(mkCell(e.note || ''));
                tr.appendChild(mkMonoCell(fmtAge(e.added_ts)));
                const td = document.createElement('td');
                td.appendChild(mkButton('✕ удалить', () => {
                    const payload = {};
                    if (e.user_id) payload.user_id = e.user_id;
                    else if (e.username) payload.username = e.username;
                    whitelistRemove(payload);
                }, 'danger'));
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
        }
        function renderBanned(items) {
            const tbody = document.getElementById('ban-body');
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (const e of items) {
                const tr = document.createElement('tr');
                tr.appendChild(mkMonoCell(e.user_id));
                tr.appendChild(mkCell(e.note || ''));
                tr.appendChild(mkMonoCell(fmtAge(e.banned_ts)));
                const td = document.createElement('td');
                td.appendChild(mkButton('↻ unban',
                    () => unbanUser(e.user_id)));
                tr.appendChild(td);
                tbody.appendChild(tr);
            }
        }
        function renderSummary(data) {
            const s = data.settings || {};
            const sum = document.getElementById('summary');
            while (sum.firstChild) sum.removeChild(sum.firstChild);
            sum.appendChild(document.createTextNode(
                'Active chats: ' + (s.active_chats_count || 0) +
                ' / ' + (s.total_chats_count || 0) +
                ' · flood threshold: ' + (s.flood_msg_limit || '?') +
                ' msg / ' + (s.flood_window_sec || '?') + 's' +
                ' · link limit: ' + (s.link_limit || '?') +
                ' · recent catches: ' + (data.count_recent || 0)));

            // Простой ASCII-style bar chart за 7 дней.
            const chart = document.getElementById('daily-chart');
            while (chart.firstChild) chart.removeChild(chart.firstChild);
            const stats = data.daily_stats || [];
            const max = Math.max(1, ...stats.map(d => d.count || 0));
            for (const d of stats) {
                const row = document.createElement('div');
                row.className = 'mono';
                row.style.fontSize = '0.78rem';
                row.style.color = 'var(--text-muted)';
                row.appendChild(document.createTextNode(d.date + '  '));
                const bar = document.createElement('span');
                bar.className = 'bar';
                bar.style.width = (Math.round((d.count / max) * 200)) + 'px';
                row.appendChild(bar);
                row.appendChild(document.createTextNode('  ' + d.count));
                chart.appendChild(row);
            }
        }
        async function refresh() {
            const errBanner = document.getElementById('err-banner');
            errBanner.textContent = '';
            try {
                const res = await fetch('/api/admin/captcha/list');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                renderSummary(data);
                renderCatches(data.recent || []);
                renderWhitelist(data.whitelist || []);
                renderBanned(data.banned_users || []);
                document.getElementById('last-update').textContent =
                    new Date().toLocaleTimeString('ru-RU', { hour12: false });
            } catch (e) {
                const banner = document.createElement('div');
                banner.className = 'err-banner';
                banner.textContent = 'Ошибка: ' + e.message;
                errBanner.appendChild(banner);
            }
        }
        refresh();
        setInterval(refresh, 30000);
    </script>
</body>
</html>
"""
